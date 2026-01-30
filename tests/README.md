# Tests

This folder contains lightweight API smoke tests that spin up in-process servers and
exercise the buyer/seller/front-end flows over TCP.

Run:
```bash
python3 -m unittest discover -s tests -v
```

Notes:
- Tests use temporary state files and do not touch `db_customer/state.json`.
- They start servers on ephemeral ports, so no local services are required.
