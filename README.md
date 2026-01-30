# PA1 Scaffold (Python + TCP)

This scaffold implements length-prefixed JSON over raw TCP with **file-backed** JSON storage in the backend DB services. Frontend servers are stateless and store no persistent per-user state.

## Structure
- `common/` shared protocol + TCP helpers
- `db_customer/` customer DB service (buyers, sellers, sessions, carts)
- `db_product/` product DB service (items, search)
- `server_buyer/` stateless buyer frontend server
- `server_seller/` stateless seller frontend server
- `client_buyer/` CLI buyer client
- `client_seller/` CLI seller client

## Wire Protocol
- 4-byte big-endian length prefix
- UTF-8 JSON payload

Each request:
```json
{"type":"Request","request_id":"1","api":"Login","data":{...}}
```
Each response:
```json
{"type":"Response","request_id":"1","ok":true,"error":null,"data":{...}}
```

## Run (local)
From `distributed-systems-lab/`:

```bash
python3 db_customer/customer_server.py --host 127.0.0.1 --port 6001 --state db_customer/state.db
python3 db_product/product_server.py --host 127.0.0.1 --port 6002 --state db_product/state.db
python3 server_buyer/buyer_server.py --host 127.0.0.1 --port 6003 --customer-host 127.0.0.1 --customer-port 6001 --product-host 127.0.0.1 --product-port 6002
python3 server_seller/seller_server.py --host 127.0.0.1 --port 6004 --customer-host 127.0.0.1 --customer-port 6001 --product-host 127.0.0.1 --product-port 6002
```

Buyer CLI:
```bash
python3 client_buyer/cli.py --host 127.0.0.1 --port 6003
```
Seller CLI:
```bash
python3 client_seller/cli.py --host 127.0.0.1 --port 6004
```

## CLI Commands
- `create <name> <password>`
- `login <name> <password>`
- `logout`
- `api <API> <json>`
- `session <session_id>`

Example:
```
create alice pass123
login alice pass123
api SearchItemsForSale {"category":1,"keywords":["book"]}
```

## Search Semantics
If keywords are provided, return items in the category with **at least one** keyword match, ordered by descending match count. If no keywords, return all items in that category with quantity > 0.

## Notes
- Session timeout is 5 minutes (enforced in `db_customer`).
- Frontends are stateless; all persistent data is stored in `db_customer/state.db` and `db_product/state.db`.
- `MakePurchase` is intentionally not implemented per PA1.
