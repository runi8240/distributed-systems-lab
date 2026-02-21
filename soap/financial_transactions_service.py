import argparse
import random
from wsgiref.simple_server import make_server

from spyne import Application, ServiceBase, Unicode, rpc
from spyne.protocol.soap import Soap11
from spyne.server.wsgi import WsgiApplication


class FinancialTransactionsService(ServiceBase):
    @rpc(Unicode, Unicode, Unicode, Unicode, _returns=Unicode)
    def process_transaction(ctx, user_name, credit_card_number, expiration_date, security_code):
        if not user_name or not credit_card_number or not expiration_date or not security_code:
            return "No"
        return "Yes" if random.random() < 0.9 else "No"


def create_app():
    return Application(
        [FinancialTransactionsService],
        tns="urn:financial.transactions",
        in_protocol=Soap11(validator="soft"),
        out_protocol=Soap11(),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8008)
    args = parser.parse_args()

    app = WsgiApplication(create_app())
    server = make_server(args.host, args.port, app)
    print(f"SOAP service listening on http://{args.host}:{args.port}")
    print(f"WSDL available at http://{args.host}:{args.port}/?wsdl")
    server.serve_forever()


if __name__ == "__main__":
    main()
