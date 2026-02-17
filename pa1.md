From home folder -
```
docker compose up --build
```

Start the seller frontend -
```
python3 client_seller/cli.py
```

Create a seller -
```
create seller1 pass1
login seller1 pass1
```

Add items to sell -
```
api RegisterItemForSale {"name":"distributed_systems","category":1,"keywords":["book"],"condition":"new","price":10,"quantity":10}
api RegisterItemForSale {"name":"design_of_data_intensive_apps","category":1,"keywords":["book"],"condition":"new","price":8,"quantity":11}
api RegisterItemForSale {"name":"autosport","category":2,"keywords":["book", "maganize"],"condition":"new","price":3,"quantity":10}
api RegisterItemForSale {"name":"nike_vomero","category":3,"keywords":["shoe"],"condition":"new","price":22,"quantity":3}
api RegisterItemForSale {"name":"tshirt1","category":4,"keywords":["clothing"],"condition":"new","price":5,"quantity":5}
api RegisterItemForSale {"name":"tshirt2","category":4,"keywords":["clothing"],"condition":"new","price":6,"quantity":10}
```

Create buyer -
```
python3 client_buyer/cli.py
create buyer1 pass1
login buyer1 pass1
```

Look for items to buy -
```
api SearchItemsForSale {"keywords":["book"]}
api SearchItemsForSale {"keywords":["clothing"]}
```

Add items to cart - 
```
api AddItemToCart {"item_id":"1:1","quantity":1}
api AddItemToCart {"item_id":"2:1","quantity":1}
api AddItemToCart {"item_id":"4:1","quantity":1}
```

Display the cart -
```
api DisplayCart {}
```

Create another buyer session (same buyer) and add other items to the cart - 

```
python3 client_buyer/cli.py
login buyer1 pass1

api AddItemToCart {"item_id":"4:1","quantity":1}
api AddItemToCart {"item_id":"2:1","quantity":1}
api AddItemToCart {"item_id":"3:1","quantity":1}
```

# Marketplace API Cheat Sheet

Use with CLI:

```bash
api <API_NAME> <JSON>
```

Notes:
- After `login`, `session_id` is auto-attached by the CLI for requests that need auth.
- For `create` and `login`, you can use either shorthand commands (`create`, `login`) or `api` form.

## Seller Frontend APIs (`server_seller`)

### `CreateAccount`
```json
{"name":"seller1","password":"pass1"}
```
```bash
api CreateAccount {"name":"seller1","password":"pass1"}
```

### `Login`
```json
{"name":"seller1","password":"pass1"}
```
```bash
api Login {"name":"seller1","password":"pass1"}
```

### `Logout`
```json
{}
```
```bash
api Logout {}
```

### `GetSellerRating`
```json
{}
```
```bash
api GetSellerRating {}
```

### `RegisterItemForSale`
```json
{"name":"book1","category":1,"keywords":["book","math"],"condition":"new","price":19.99,"quantity":5}
```
```bash
api RegisterItemForSale {"name":"book1","category":1,"keywords":["book","math"],"condition":"new","price":19.99,"quantity":5}
```

### `ChangeItemPrice`
```json
{"item_id":"1:1","price":24.5}
```
```bash
api ChangeItemPrice {"item_id":"1:1","price":24.5}
```

### `UpdateUnitsForSale`
```json
{"item_id":"1:1","quantity_delta":3}
```
```bash
api UpdateUnitsForSale {"item_id":"1:1","quantity_delta":3}
```

### `DisplayItemsForSale`
```json
{}
```
```bash
api DisplayItemsForSale {}
```

### `Ping`
```json
{}
```
```bash
api Ping {}
```

## Buyer Frontend APIs (`server_buyer`)

### `CreateAccount`
```json
{"name":"buyer1","password":"pass1"}
```
```bash
api CreateAccount {"name":"buyer1","password":"pass1"}
```

### `Login`
```json
{"name":"buyer1","password":"pass1"}
```
```bash
api Login {"name":"buyer1","password":"pass1"}
```

### `Logout`
```json
{}
```
```bash
api Logout {}
```

### `SearchItemsForSale`
```json
{"keywords":["book"]}
```
```bash
api SearchItemsForSale {"keywords":["book"]}
```

### `SearchItemsForSale` (with category)
```json
{"category":1,"keywords":["book","math"]}
```
```bash
api SearchItemsForSale {"category":1,"keywords":["book","math"]}
```

### `GetItem`
```json
{"item_id":"1:1"}
```
```bash
api GetItem {"item_id":"1:1"}
```

### `AddItemToCart`
```json
{"item_id":"1:1","quantity":2}
```
```bash
api AddItemToCart {"item_id":"1:1","quantity":2}
```

### `RemoveItemFromCart`
```json
{"item_id":"1:1","quantity":1}
```
```bash
api RemoveItemFromCart {"item_id":"1:1","quantity":1}
```

### `SaveCart`
```json
{}
```
```bash
api SaveCart {}
```

### `ClearCart`
```json
{}
```
```bash
api ClearCart {}
```

### `DisplayCart`
```json
{}
```
```bash
api DisplayCart {}
```

### `ProvideFeedback`
```json
{"item_id":"1:1","vote":"up"}
```
```bash
api ProvideFeedback {"item_id":"1:1","vote":"up"}
```

### `GetSellerRating`
```json
{"seller_id":1}
```
```bash
api GetSellerRating {"seller_id":1}
```

### `GetBuyerPurchases`
```json
{}
```
```bash
api GetBuyerPurchases {}
```

### `Ping`
```json
{}
```
```bash
api Ping {}
```
