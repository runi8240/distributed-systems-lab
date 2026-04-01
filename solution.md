# Distributed Marketplace Solution

## 1. Overview

This repository implements a distributed online marketplace for buyers and sellers. The system is split into multiple services so that client-facing logic, customer/session data, product/inventory data, and payment authorization are handled by different components.

The current PA2-style solution uses:

- REST/HTTP between clients and the frontend services
- gRPC between frontend services and backend database services
- SOAP/WSDL for the external financial transaction service
- SQLite for persistent state in the two backend services
- Docker Compose for local multi-service execution
- GCP VM scripts for deployment with one main service per VM

At a high level, the architecture is:

1. A seller client talks to the seller frontend over HTTP.
2. A buyer client talks to the buyer frontend over HTTP.
3. The frontends delegate durable operations to backend services over gRPC.
4. The buyer frontend calls a SOAP financial service during purchase.
5. Persistent state is stored only in the backend database services.

This produces a deliberately distributed design with clear service boundaries and different communication technologies across tiers.

## 2. Service Breakdown

### 2.1 `server_buyer`

`server_buyer/buyer_server.py` is the buyer-facing Flask service running by default on port `6003`.

Responsibilities:

- create buyer accounts
- log buyers in and out
- validate buyer sessions by consulting the customer database service
- search items and fetch item details through the product database service
- manage buyer carts
- save and clear carts
- submit purchases
- collect item feedback
- read seller ratings
- read buyer purchase counts

This service is stateless in the sense that it does not persist marketplace state locally. It orchestrates calls to:

- `db_customer` for users, sessions, carts, and ratings
- `db_product` for items, inventory, search, and product feedback
- the SOAP service for payment authorization

### 2.2 `server_seller`

`server_seller/seller_server.py` is the seller-facing Flask service running by default on port `6004`.

Responsibilities:

- create seller accounts
- log sellers in and out
- validate seller sessions via `db_customer`
- list the current seller’s items
- register new items for sale through `db_product`

This service is thinner than the buyer frontend. It acts mostly as an authenticated HTTP facade over gRPC calls.

### 2.3 `db_customer`

`db_customer/customer_server.py` is the gRPC backend for customer and session state, running by default on port `6001`.

Responsibilities:

- store buyers and sellers
- authenticate login requests
- create and validate sessions
- enforce session timeout
- manage durable carts and session-local carts
- store seller feedback counters
- store buyer purchase counts metadata

It uses SQLite with WAL mode enabled and protects operations with a process-level `threading.Lock`, which keeps the logic simple and avoids concurrent write corruption inside a single service instance.

### 2.4 `db_product`

`db_product/product_server.py` is the gRPC backend for product and inventory state, running by default on port `6002`.

Responsibilities:

- register items
- assign item IDs
- store item metadata and keywords
- change price
- update inventory counts
- return a seller’s current catalog
- support keyword-based search
- return item details
- collect item feedback
- check availability before cart add or purchase

Like `db_customer`, it uses SQLite in WAL mode plus a process-level lock to serialize critical operations.

### 2.5 SOAP Financial Service

`soap/financial_transactions_service.py` exposes a SOAP 1.1 service, by default on port `8008`.

Responsibilities:

- accept payment information during purchase
- return `"Yes"` or `"No"`

The implementation is intentionally simple:

- returns `"No"` if required fields are missing
- otherwise returns `"Yes"` with 90% probability and `"No"` with 10% probability

This models an external bank/payment system and gives the buyer server a third integration style beyond REST and gRPC.

## 3. Communication Model

### 3.1 Client to Frontend: REST over HTTP

The CLI clients in:

- `client_buyer/cli.py`
- `client_seller/cli.py`

use `common/cli.py`, which maps high-level marketplace commands to HTTP endpoints.

Examples:

- `POST /buyer/accounts`
- `POST /buyer/login`
- `GET /buyer/items/search`
- `POST /buyer/cart/items`
- `POST /buyer/purchases`
- `POST /seller/accounts`
- `POST /seller/login`
- `GET /seller/items`
- `POST /seller/items`

Responses are normalized into a common JSON shape:

```json
{
  "ok": true,
  "data": { "...": "..." },
  "error": null
}
```

or

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "SOME_CODE",
    "message": "human readable explanation"
  }
}
```

HTTP status codes are mapped from logical error codes. For example:

- authentication/session failures become `401`
- authorization failures become `403`
- missing resources become `404`
- most input/logic errors remain `400`

### 3.2 Frontend to Backend: gRPC

The frontends use `common/grpc_db_client.py` to call the backend database services via gRPC. The protocol contract is defined in `common/protos/marketplace.proto`, and generated client/server stubs live under `common/grpc_gen/`.

Two gRPC services exist:

- `CustomerDBService`
- `ProductDBService`

This tier separation is the core PA2 change relative to a single-process design. Frontends translate HTTP requests into gRPC requests and then translate gRPC responses back into HTTP responses.

### 3.3 Buyer Frontend to Payment Service: SOAP

The buyer frontend constructs a Zeep SOAP client from a WSDL URL and calls:

- `process_transaction(user_name, credit_card_number, expiration_date, security_code)`

This happens only during the purchase path.

## 4. Data Model

### 4.1 Customer Database Schema

`db_customer` creates and uses the following SQLite tables:

#### `buyers`

- `id`
- `name`
- `password`
- `purchases_count`
- `cart_saved`

#### `sellers`

- `id`
- `name`
- `password`
- `feedback_up`
- `feedback_down`
- `items_sold`

#### `sessions`

- `session_id`
- `role`
- `user_id`
- `last_active`
- `cart_initialized`

#### `cart_items`

Durable cart contents for a buyer.

- `buyer_id`
- `item_id`
- `quantity`

#### `session_cart_items`

Session-specific cart state.

- `session_id`
- `item_id`
- `quantity`

### 4.2 Product Database Schema

`db_product` creates and uses:

#### `items`

- `item_id`
- `name`
- `category`
- `seq`
- `condition`
- `price`
- `quantity`
- `seller_id`
- `feedback_up`
- `feedback_down`

#### `item_keywords`

- `item_id`
- `keyword`

An index is created on item category and on keyword values to support lookup.

## 5. Identity, Sessions, and Authentication

### 5.1 Account Creation

Buyer and seller accounts are created through the frontend services and stored in `db_customer`.

Validation includes:

- non-empty name
- non-empty password
- username length at most 32 characters

The current implementation does not enforce unique usernames. Login looks up the first row matching the given name and checks the password.

### 5.2 Login

When login succeeds:

- the backend creates a UUID session ID
- the role and numeric user ID are stored in `sessions`
- `last_active` is set to the current time
- `cart_initialized` is set to `0`

### 5.3 Session Validation

Every protected frontend operation calls `ValidateSession` through `db_customer`.

Checks include:

- session ID exists
- role matches the frontend being used
- session has not expired

Session timeout is currently:

- `5 * 60` seconds = 5 minutes

On successful validation, `last_active` is refreshed.

If the session has expired:

- the session row is deleted
- any `session_cart_items` rows for that session are deleted
- the caller receives `SESSION_TIMEOUT`

## 6. Cart Design

One of the more interesting parts of the solution is the cart model. The system supports both:

- a durable buyer cart
- an in-session working cart

### 6.1 Durable Cart

`cart_items` stores the buyer’s persisted cart.

This is the version a new session sees before it starts making changes.

### 6.2 Session Cart

`session_cart_items` stores modifications made inside a specific active session.

When a buyer session first mutates the cart:

1. the service copies the durable cart into `session_cart_items`
2. marks `cart_initialized = 1` in the session
3. applies updates only to the session cart

This gives each login session an isolated working copy until the user explicitly saves.

### 6.3 Why This Matters

This behavior explains the demo in `pa2.md`:

- one session adds items
- another session for the same buyer does not immediately see them
- after `SaveCart`, the second session sees the saved cart contents

So the system implements session-local cart editing plus explicit persistence.

### 6.4 Cart Operations

Supported operations:

- `GetCart`
- `UpdateCart`
- `ClearCart`
- `SaveCart`
- `ClearAndSaveCart`

Important semantics:

- adding/removing items in a session affects only that session copy after initialization
- `SaveCart` overwrites the durable cart with the session cart
- `ClearCart` clears the session cart if a session is active, otherwise the durable cart
- `ClearAndSaveCart` clears the durable cart and the current session cart, which is used after successful purchase

## 7. Product and Inventory Design

### 7.1 Item Registration

Sellers register items through `server_seller`, which calls `db_product.RegisterItem`.

Each item stores:

- name
- category
- keywords
- condition
- price
- quantity
- seller ID
- feedback counters

### 7.2 Item ID Scheme

Item IDs are generated as:

- `<category>:<sequence-within-category>`

Examples:

- `1:1`
- `2:4`

This is implemented by keeping a per-category sequence value in the `items` table and incrementing the maximum `seq` already present for that category.

### 7.3 Keyword Rules

The product service validates keywords with these constraints:

- at most 5 keywords
- each keyword must be a string
- each keyword must be at most 8 characters

### 7.4 Search

The buyer frontend exposes search through `GET /buyer/items/search`.

The product backend:

1. filters to items with `quantity > 0`
2. optionally filters by category
3. computes a keyword match score based on overlap between query keywords and item keywords
4. returns items sorted by score descending

This is a simple relevance model, but it is deterministic and easy to explain.

### 7.5 Inventory Updates

Inventory changes are applied through `UpdateUnitsForSale`.

The service prevents negative inventory by rejecting updates that would bring quantity below zero.

Availability checks are separate and used in two places:

- before adding an item to a cart
- before attempting a purchase

## 8. Buyer Flow

### 8.1 Search and Read

A buyer can:

- search items by keyword, optionally by category
- fetch a single item by item ID
- read seller rating counters
- read their purchase count

### 8.2 Add to Cart

When a buyer adds to cart, the buyer frontend:

1. validates the session
2. checks product availability through `db_product.CheckAvailability`
3. if enough stock exists, updates the cart through `db_customer.UpdateCart`

This prevents obviously impossible cart additions, although it is still a non-atomic multi-step workflow.

### 8.3 Remove / Clear / Save

The buyer can:

- remove a quantity from a specific item
- clear the cart
- save the session cart into the durable cart

### 8.4 Make Purchase

Purchase is the most complex request in the system.

The buyer frontend performs these steps:

1. validate the session
2. validate payment fields locally
3. fetch the current cart from `db_customer`
4. reject if the cart is empty
5. check inventory for each cart item through `db_product.CheckAvailability`
6. call the SOAP transaction service
7. if the bank says `"Yes"`, decrement inventory item by item
8. if an inventory update fails after partial success, roll back prior decrements
9. clear the cart via `db_customer.ClearAndSaveCart`
10. return success with purchased items and any warnings

### 8.5 Payment Validation

Before calling the bank, the buyer frontend validates:

- non-empty user name
- credit card number contains 13 to 19 digits after removing spaces/hyphens
- credit card number passes the Luhn checksum
- security code has 3 or 4 digits
- expiration date is in `MM/YY` or `MM/YYYY`
- expiration month is valid
- expiration date is not in the past

This is a useful design choice because invalid cards are rejected before spending effort on remote payment authorization.

## 9. Seller Flow

The seller path is simpler:

1. create account
2. login
3. register items for sale
4. display current items for sale
5. logout

The current REST seller frontend exposes item registration and listing. The gRPC product backend supports additional operations like `ChangeItemPrice` and `UpdateUnitsForSale`, but those operations are not exposed by the current Flask seller frontend routes.

That means the repository contains a richer backend API surface than the current public seller HTTP API.

## 10. Feedback and Ratings

There are two distinct feedback models in the repository:

### 10.1 Item Feedback

Stored in `db_product.items` as:

- `feedback_up`
- `feedback_down`

Buyers can submit:

- `"up"`
- `"down"`

### 10.2 Seller Rating

Stored in `db_customer.sellers` as:

- `feedback_up`
- `feedback_down`

The system can return seller ratings through `GetSellerRating`.

However, in the current implementation there is no code path that updates seller feedback counters during buyer feedback submission. Buyer feedback currently updates product/item feedback, not seller rating counters. So seller ratings exist structurally, but they are effectively static unless modified elsewhere.

## 11. Error Handling Strategy

The solution uses logical error codes instead of only raw HTTP status codes.

Common codes include:

- `INVALID_ARGUMENT`
- `AUTH_FAILED`
- `NOT_LOGGED_IN`
- `NOT_AUTHORIZED`
- `SESSION_TIMEOUT`
- `NOT_FOUND`
- `OUT_OF_STOCK`
- `INVALID_PAYMENT_INFO`
- `BANK_UNAVAILABLE`
- `PAYMENT_DECLINED`
- `PURCHASE_FAILED`
- `UNIMPLEMENTED`
- `UNAVAILABLE`

This is helpful because:

- backend services can communicate clear failure causes
- frontends can translate them consistently
- clients get structured error details

## 12. Concurrency and Consistency

### 12.1 Local Concurrency

Both database services run gRPC servers with thread pools sized to 32 workers. SQLite access is guarded with a single Python lock inside each service, which serializes operations that touch the shared database connection.

This is simple and correct for a single service instance, but it limits backend write parallelism.

### 12.2 Purchase Consistency

The purchase path is not a distributed transaction across services. Instead, it uses compensating behavior:

- inventory is decremented one item at a time
- if a later decrement fails, previous decrements are rolled back

This is a pragmatic partial rollback strategy, but it is not equivalent to a true atomic transaction across `db_product`, `db_customer`, and the SOAP service.

### 12.3 Cart Consistency

The session cart design provides isolation between concurrent sessions for the same buyer until an explicit save occurs. This is a deliberate consistency model rather than shared live cart synchronization.

## 13. Deployment Story

### 13.1 Local Development with Docker Compose

`docker-compose.yml` defines:

- `db_customer`
- `db_product`
- `server_buyer`
- `server_seller`
- `soap_financial`
- optional interactive `client_buyer`
- optional interactive `client_seller`

Default exposed ports:

- `6001` customer DB gRPC
- `6002` product DB gRPC
- `6003` buyer REST frontend
- `6004` seller REST frontend
- `8008` SOAP service

### 13.2 GCP VM Deployment

The scripts under `scripts/gcp-pa2/` automate PA2 deployment onto Google Compute Engine.

#### `create_vms.sh`

Creates:

- `pa2-db-customer`
- `pa2-db-product`
- `pa2-server-buyer`
- `pa2-server-seller`
- optionally `pa2-soap-financial`

Also creates firewall rules so:

- internal services can talk to each other on `6001-6004` and `8008`
- buyer frontend is publicly reachable on `6003`
- seller frontend is publicly reachable on `6004`

#### `configure_services.sh`

Deploys the current repository to the VMs by:

- copying a tarball of the repo
- installing Python and dependencies
- creating a virtual environment
- wiring services to the correct internal IPs
- creating `systemd` units
- starting services automatically

This is the main automation path for a one-service-per-VM distributed deployment.

### 13.3 Kubernetes Manifests

The `k8s/` directory contains deployment/service YAML files for the four core marketplace services. These provide an alternate deployment direction, although the GCP VM scripts are the more complete and clearly maintained deployment path in this repository.

## 14. Client Tooling

The CLI in `common/cli.py` provides a shared REPL for buyer and seller clients.

Supported commands include:

- `create <name> <password>`
- `login <name> <password>`
- `logout`
- `api <API> <json>`
- `makepurchase` for buyers
- `session <id>`

This REPL is useful for manual demos because it:

- persists the current session ID in memory
- automatically injects the session ID into many API calls
- prints structured JSON responses

## 15. Protobuf and Code Generation

`common/protos/marketplace.proto` defines the gRPC contract for both backend services.

`scripts/gen_protos.sh` regenerates:

- `common/grpc_gen/marketplace_pb2.py`
- `common/grpc_gen/marketplace_pb2_grpc.py`

This keeps the typed service definitions centralized and avoids hand-written wire protocols between the REST frontends and backend services.

## 16. Benchmarking and Performance Results

The repository includes a benchmark harness in `scripts/bench/run_scenarios.py`.

It measures:

- average response time
- average throughput

Scenarios:

- 1 buyer + 1 seller
- 10 buyers + 10 sellers
- 100 buyers + 100 sellers

Measured APIs:

- buyer load uses `SearchItemsForSale`
- seller load uses `DisplayItemsForSale`

The benchmark writes machine-readable JSON and report-ready Markdown into `scripts/bench/results/`.

The current `REPORT.md` records:

| Scenario | Buyers + Sellers | Clients | Avg Response Time (s) | Avg Throughput (ops/s) |
|---|---:|---:|---:|---:|
| 1 | 1 + 1 | 2 | 0.098011 | 20.34 |
| 2 | 10 + 10 | 20 | 0.114528 | 171.49 |
| 3 | 100 + 100 | 200 | 0.284379 | 656.96 |

The observed behavior is what we would expect from this architecture:

- latency rises as concurrency increases
- throughput rises with concurrency but sub-linearly
- extra network hops and protocol translation increase overhead relative to a simpler monolithic or single-tier design

## 17. Strengths of the Solution

- Clear separation of concerns between customer/session state and product/inventory state.
- Deliberate use of multiple distributed-system communication styles: REST, gRPC, and SOAP.
- Stateless frontends with backend-owned durable state.
- Reasonable input validation for payment data.
- Session timeout and role-based access checks.
- Session-local cart isolation with explicit save semantics.
- Practical deployment automation for GCP VMs.
- Built-in benchmarking to support performance analysis.

## 18. Important Limitations and Gaps

Several details in the repository are worth documenting explicitly.

### 18.1 No True Distributed Transaction

Purchase spans multiple systems but does not use a two-phase commit or other transactional protocol. It relies on:

- pre-checking availability
- making the bank call
- decrementing inventory
- compensating rollback if a later decrement fails

This is acceptable for a class project but not strong enough for a production financial workflow.

### 18.2 Some Stored Fields Are Not Fully Used

Examples:

- `buyers.purchases_count` exists, but purchase success does not increment it
- `sellers.feedback_up/down` exist, but buyer feedback currently updates item feedback instead
- `buyers.cart_saved` and `sellers.items_sold` are present but not actively maintained in the main flows

So the schema is somewhat broader than the active logic.

### 18.3 Username Uniqueness Is Not Enforced

Duplicate buyer or seller names can exist. Login picks the first matching record by name.

### 18.4 Seller REST API Is Narrower Than the gRPC Backend

The product backend supports:

- changing price
- changing quantity

but the current Flask seller frontend exposes only:

- item registration
- item listing

### 18.5 Test Suite Contains Legacy Assumptions

`tests/test_api_smoke.py` references TCP request helpers and handler factories that do not match the current Flask REST frontend implementation. That means the test file does not reflect the present runtime architecture cleanly and should be treated as legacy or in need of refresh.

## 19. End-to-End Example

An end-to-end usage path looks like this:

1. Seller creates an account through `server_seller`.
2. Seller logs in and receives a session ID from `db_customer`.
3. Seller registers items for sale through `server_seller`, which writes them into `db_product`.
4. Buyer creates an account and logs in through `server_buyer`.
5. Buyer searches items through `server_buyer`, which queries `db_product`.
6. Buyer adds items to the cart; `server_buyer` verifies stock with `db_product` and stores cart changes in `db_customer`.
7. Buyer optionally saves the cart so another session for the same buyer can see it.
8. Buyer starts `MakePurchase`.
9. `server_buyer` validates card details, loads the cart, rechecks stock, and calls the SOAP bank service.
10. If approved, `server_buyer` decrements inventory in `db_product` and clears the cart in `db_customer`.
11. The frontend returns a success payload containing purchased items.

## 20. Summary

This solution is a distributed marketplace implementation organized around two REST frontends, two gRPC backend services, and one SOAP payment service. The design demonstrates service decomposition, protocol translation, remote procedure calls, session management, cart persistence, inventory handling, and multi-VM deployment.

Its strongest technical ideas are the separation of frontend and backend responsibilities, the session-specific cart model, and the explicit use of different communication technologies across system boundaries. Its main limitations are the lack of a true cross-service transaction mechanism and a few partially implemented schema features. Even with those tradeoffs, it is a coherent and working distributed-systems lab solution with clear architectural intent.
