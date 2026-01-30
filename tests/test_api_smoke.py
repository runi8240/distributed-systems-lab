import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.append(ROOT)
TESTS_DIR = os.path.join(ROOT, "tests")
if TESTS_DIR not in sys.path:
    sys.path.append(TESTS_DIR)

from common.tcp_client import tcp_request
from db_customer.customer_server import handle_request_factory as customer_handler_factory
from db_product.product_server import handle_request_factory as product_handler_factory
from server_buyer.buyer_server import handle_request_factory as buyer_handler_factory
from server_seller.seller_server import handle_request_factory as seller_handler_factory
from helpers import ThreadedServer


def _request(host, port, api, data=None, request_id="1"):
    return tcp_request(
        host,
        port,
        {
            "type": "Request",
            "request_id": request_id,
            "api": api,
            "data": data or {},
        },
    )


class APISmokeTest(unittest.TestCase):
    def _assert_ok(self, resp):
        self.assertTrue(resp.get("ok"), msg=f"expected ok response, got: {resp}")

    def _seller_login(self, name="alice", password="pass"):
        _request(self.seller.host, self.seller.port, "CreateAccount", {"name": name, "password": password})
        login = _request(self.seller.host, self.seller.port, "Login", {"name": name, "password": password})
        self._assert_ok(login)
        return login["data"]["session_id"]

    def _buyer_login(self, name="bob", password="pass"):
        _request(self.buyer.host, self.buyer.port, "CreateAccount", {"name": name, "password": password})
        login = _request(self.buyer.host, self.buyer.port, "Login", {"name": name, "password": password})
        self._assert_ok(login)
        return login["data"]["session_id"]

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        customer_state = os.path.join(cls._tmpdir.name, "customer_state.db")
        product_state = os.path.join(cls._tmpdir.name, "product_state.db")

        cls.customer = ThreadedServer("127.0.0.1", 0, customer_handler_factory(customer_state))
        cls.product = ThreadedServer("127.0.0.1", 0, product_handler_factory(product_state))
        cls.buyer = ThreadedServer(
            "127.0.0.1",
            0,
            buyer_handler_factory(cls.customer.host, cls.customer.port, cls.product.host, cls.product.port),
        )
        cls.seller = ThreadedServer(
            "127.0.0.1",
            0,
            seller_handler_factory(cls.customer.host, cls.customer.port, cls.product.host, cls.product.port),
        )

        # Seed a seller and item for buyer tests.
        seller = _request(cls.seller.host, cls.seller.port, "CreateAccount", {"name": "alice", "password": "pass"})
        if not seller.get("ok"):
            raise RuntimeError(f"CreateAccount failed: {seller}")
        cls.seller_id = seller["data"]["seller_id"]
        login = _request(cls.seller.host, cls.seller.port, "Login", {"name": "alice", "password": "pass"})
        if not login.get("ok"):
            raise RuntimeError(f"Login failed: {login}")
        cls.seller_session = login["data"]["session_id"]
        reg = _request(
            cls.seller.host,
            cls.seller.port,
            "RegisterItemForSale",
            {
                "session_id": cls.seller_session,
                "name": "Intro to DS",
                "category": 1,
                "keywords": ["book", "cs"],
                "condition": "new",
                "price": 19.99,
                "quantity": 5,
            },
        )
        if not reg.get("ok"):
            raise RuntimeError(f"RegisterItemForSale failed: {reg}")
        cls.item_id = reg["data"]["item_id"]

    @classmethod
    def tearDownClass(cls):
        cls.seller.stop()
        cls.buyer.stop()
        cls.product.stop()
        cls.customer.stop()
        cls._tmpdir.cleanup()

    def test_backend_customer_apis(self):
        ping = _request(self.customer.host, self.customer.port, "Ping")
        self._assert_ok(ping)

        buyer = _request(self.customer.host, self.customer.port, "CreateBuyer", {"name": "bob", "password": "pass"})
        self._assert_ok(buyer)
        buyer_id = buyer["data"]["buyer_id"]

        seller = _request(self.customer.host, self.customer.port, "CreateSeller", {"name": "alice2", "password": "pass"})
        self._assert_ok(seller)
        seller_id = seller["data"]["seller_id"]

        login = _request(self.customer.host, self.customer.port, "Login", {"role": "buyer", "name": "bob", "password": "pass"})
        self._assert_ok(login)
        session_id = login["data"]["session_id"]

        valid = _request(self.customer.host, self.customer.port, "ValidateSession", {"session_id": session_id})
        self._assert_ok(valid)

        rating = _request(self.customer.host, self.customer.port, "GetSellerRating", {"seller_id": seller_id})
        self._assert_ok(rating)

        cart = _request(self.customer.host, self.customer.port, "GetCart", {"buyer_id": buyer_id})
        self._assert_ok(cart)

        upd = _request(
            self.customer.host,
            self.customer.port,
            "UpdateCart",
            {"buyer_id": buyer_id, "item_id": self.item_id, "quantity_delta": 1},
        )
        self._assert_ok(upd)

        clear = _request(self.customer.host, self.customer.port, "ClearCart", {"buyer_id": buyer_id})
        self._assert_ok(clear)

        purchases = _request(self.customer.host, self.customer.port, "GetBuyerPurchases", {"buyer_id": buyer_id})
        self._assert_ok(purchases)
        self.assertIn("purchases_count", purchases["data"])

        logout = _request(self.customer.host, self.customer.port, "Logout", {"session_id": session_id})
        self._assert_ok(logout)

    def test_backend_product_apis(self):
        ping = _request(self.product.host, self.product.port, "Ping")
        self._assert_ok(ping)

        reg = _request(
            self.product.host,
            self.product.port,
            "RegisterItem",
            {
                "name": "Algo Book",
                "category": 2,
                "keywords": ["algo"],
                "condition": "used",
                "price": 10.0,
                "quantity": 3,
                "seller_id": self.seller_id,
            },
        )
        self._assert_ok(reg)
        item_id = reg["data"]["item_id"]

        price = _request(self.product.host, self.product.port, "ChangeItemPrice", {"item_id": item_id, "price": 12.0})
        self._assert_ok(price)

        units = _request(
            self.product.host,
            self.product.port,
            "UpdateUnitsForSale",
            {"item_id": item_id, "quantity_delta": 1},
        )
        self._assert_ok(units)

        display = _request(self.product.host, self.product.port, "DisplayItemsForSale", {"seller_id": self.seller_id})
        self._assert_ok(display)

        search = _request(self.product.host, self.product.port, "SearchItems", {"keywords": ["algo"]})
        self._assert_ok(search)

        get_item = _request(self.product.host, self.product.port, "GetItem", {"item_id": item_id})
        self._assert_ok(get_item)

        feedback = _request(
            self.product.host,
            self.product.port,
            "ProvideFeedback",
            {"item_id": item_id, "vote": "up"},
        )
        self._assert_ok(feedback)

        avail = _request(
            self.product.host,
            self.product.port,
            "CheckAvailability",
            {"item_id": item_id, "quantity": 1},
        )
        self._assert_ok(avail)

    def test_seller_interface_apis(self):
        ping = _request(self.seller.host, self.seller.port, "Ping")
        self._assert_ok(ping)

        session_id = self._seller_login(name="alice3")

        reg = _request(
            self.seller.host,
            self.seller.port,
            "RegisterItemForSale",
            {
                "session_id": session_id,
                "name": "Networks",
                "category": 3,
                "keywords": ["net"],
                "condition": "new",
                "price": 18.0,
                "quantity": 2,
            },
        )
        self._assert_ok(reg)
        item_id = reg["data"]["item_id"]

        price = _request(
            self.seller.host,
            self.seller.port,
            "ChangeItemPrice",
            {"session_id": session_id, "item_id": item_id, "price": 20.0},
        )
        self._assert_ok(price)

        units = _request(
            self.seller.host,
            self.seller.port,
            "UpdateUnitsForSale",
            {"session_id": session_id, "item_id": item_id, "quantity_delta": 1},
        )
        self._assert_ok(units)

        items = _request(self.seller.host, self.seller.port, "DisplayItemsForSale", {"session_id": session_id})
        self._assert_ok(items)

        rating = _request(self.seller.host, self.seller.port, "GetSellerRating", {"session_id": session_id})
        self._assert_ok(rating)

        logout = _request(self.seller.host, self.seller.port, "Logout", {"session_id": session_id})
        self._assert_ok(logout)

    def test_buyer_interface_apis(self):
        ping = _request(self.buyer.host, self.buyer.port, "Ping")
        self._assert_ok(ping)

        session_id = self._buyer_login(name="bob2")

        search = _request(self.buyer.host, self.buyer.port, "SearchItemsForSale", {"keywords": ["book"]})
        self._assert_ok(search)

        get_item = _request(self.buyer.host, self.buyer.port, "GetItem", {"item_id": self.item_id})
        self._assert_ok(get_item)

        add = _request(
            self.buyer.host,
            self.buyer.port,
            "AddItemToCart",
            {"session_id": session_id, "item_id": self.item_id, "quantity": 1},
        )
        self._assert_ok(add)

        remove = _request(
            self.buyer.host,
            self.buyer.port,
            "RemoveItemFromCart",
            {"session_id": session_id, "item_id": self.item_id, "quantity": 1},
        )
        self._assert_ok(remove)

        save = _request(self.buyer.host, self.buyer.port, "SaveCart", {"session_id": session_id})
        self._assert_ok(save)

        clear = _request(self.buyer.host, self.buyer.port, "ClearCart", {"session_id": session_id})
        self._assert_ok(clear)

        cart = _request(self.buyer.host, self.buyer.port, "DisplayCart", {"session_id": session_id})
        self._assert_ok(cart)

        feedback = _request(
            self.buyer.host,
            self.buyer.port,
            "ProvideFeedback",
            {"session_id": session_id, "item_id": self.item_id, "vote": "up"},
        )
        self._assert_ok(feedback)

        rating = _request(
            self.buyer.host,
            self.buyer.port,
            "GetSellerRating",
            {"session_id": session_id, "seller_id": self.seller_id},
        )
        self._assert_ok(rating)

        purchases = _request(self.buyer.host, self.buyer.port, "GetBuyerPurchases", {"session_id": session_id})
        self._assert_ok(purchases)

        logout = _request(self.buyer.host, self.buyer.port, "Logout", {"session_id": session_id})
        self._assert_ok(logout)
