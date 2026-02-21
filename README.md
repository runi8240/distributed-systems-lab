## System Design, Assumptions, and Current State

This PA2 system is an online marketplace split into four server components: `server_buyer`, `server_seller`, `db_customer`, and `db_product`.

Buyer and seller clients call the frontend services using REST over HTTP, and frontends call backend databases using gRPC.

A SOAP/WSDL financial transaction service is integrated with `MakePurchase` and returns Yes/No for payment authorization.

Frontend services are stateless by design; all durable state (accounts, sessions, cart, items, feedback) is stored in backend databases.

Deployment target is GCP with one server component per VM, exposed on ports 6001-6004, while clients run locally on macOS.

Assumptions: stable TCP/IP connectivity between VMs, firewall rules permit required ports, and VM sizes are sufficient for target concurrency.

What works: account creation/login/logout, item registration and search, cart operations, feedback/rating, purchase flow, gRPC integration, and cloud deployment scripts.

Performance details and analysis are documented in `REPORT.md`.
