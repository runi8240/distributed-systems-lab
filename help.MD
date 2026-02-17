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
