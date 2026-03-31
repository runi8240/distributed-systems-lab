import os
import sys

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from common.cli import repl


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6004)
    parser.add_argument("--replicas", default="")
    args = parser.parse_args()
    if args.replicas:
        replicas = []
        for raw in args.replicas.split(","):
            raw = raw.strip()
            if not raw:
                continue
            host, port = raw.rsplit(":", 1)
            replicas.append((host, int(port)))
    else:
        replicas = [(args.host, args.port)]
    repl(replicas, "seller")


if __name__ == "__main__":
    main()
