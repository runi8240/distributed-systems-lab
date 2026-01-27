import os
import sys

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.append(_ROOT)

from common.cli import repl


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6003)
    args = parser.parse_args()
    repl(args.host, args.port, "buyer")


if __name__ == "__main__":
    main()
