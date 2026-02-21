### Setup
If needed locally -
```
docker compose up --build
```

---
If needed on gcp -
First create a new project on GCP
```
gcloud auth login
gcloud config set project <NEW_PROJECT_ID>

gcloud services enable compute.googleapis.com
gcloud config set compute/zone us-central1-a
```

Create VMs and firewall rules -
```
scripts/gcp-pa2/create_vms.sh <NEW_PROJECT_ID>
```

Deploy services to VMs -
```
scripts/gcp-pa2/configure_services.sh <NEW_PROJECT_ID>
```

Get buyer and seller IPs -
```
BUYER_IP=$(gcloud compute instances describe pa2-server-buyer --format='value(networkInterfaces[0].accessConfigs[0].natIP)')
SELLER_IP=$(gcloud compute instances describe pa2-server-seller --format='value(networkInterfaces[0].accessConfigs[0].natIP)')
```

Run clients locally -
```
python3 client_buyer/cli.py --host "$BUYER_IP" --port 6003
python3 client_seller/cli.py --host "$SELLER_IP" --port 6004
```

Run benchmark like the report -
```
python3 scripts/bench/run_scenarios.py --buyer-host "$BUYER_IP" --seller-host "$SELLER_IP"
```

---
### Demo
Seller  -
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

Buyer -
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

Create another buyer session with the same buyer and view the cart (it won't be visible)
```
python3 client_buyer/cli.py
login buyer1 pass1

api DisplayCart {}
```

Go back to the first session and save the cart -
```
api SaveCart {}
```

Now if you go to the second session then the cart will be loaded

Make the purchase -
```
api MakePurchase {"user_name":"buyer1","credit_card_number":"4111111111111111","expiration_date":"12/2030","security_code":"123"}
```

Now both sessions will have a cleared cart

---
