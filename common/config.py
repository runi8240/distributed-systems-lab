from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceConfig:
    host: str
    port: int


DEFAULTS = {
    "db_customer": ServiceConfig("127.0.0.1", 6001),
    "db_product": ServiceConfig("127.0.0.1", 6002),
    "server_buyer": ServiceConfig("127.0.0.1", 6003),
    "server_seller": ServiceConfig("127.0.0.1", 6004),
}
