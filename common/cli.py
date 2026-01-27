import argparse
import json
import socket
import sys
from typing import Any, Dict

from .protocol import recv_msg, send_msg


def _send(host: str, port: int, req: Dict[str, Any]) -> Dict[str, Any]:
    with socket.create_connection((host, port)) as sock:
        send_msg(sock, req)
        return recv_msg(sock)


def repl(host: str, port: int, role: str):
    session_id = None
    req_id = 1

    print(f"Connected to {host}:{port} as {role} client")
    print("Commands: help, create <name> <password>, login <name> <password>, logout, api <API> <json>, session <id>, exit")

    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if not line:
            continue
        if line in ("exit", "quit"):
            break
        if line == "help":
            print("create <name> <password>")
            print("login <name> <password>")
            print("logout")
            print("api <API> <json>")
            print("session <id>")
            continue

        parts = line.split(" ", 2)
        cmd = parts[0]

        if cmd == "session" and len(parts) >= 2:
            session_id = parts[1]
            print(f"session_id set to {session_id}")
            continue

        if cmd == "create" and len(parts) >= 3:
            api = "CreateAccount"
            data = {"name": parts[1], "password": parts[2]}
        elif cmd == "login" and len(parts) >= 3:
            api = "Login"
            data = {"name": parts[1], "password": parts[2]}
        elif cmd == "logout":
            api = "Logout"
            data = {"session_id": session_id}
        elif cmd == "api" and len(parts) >= 3:
            api = parts[1]
            try:
                data = json.loads(parts[2])
            except json.JSONDecodeError as e:
                print(f"invalid json: {e}")
                continue
        else:
            print("unknown command; type 'help'")
            continue

        if session_id and isinstance(data, dict) and "session_id" not in data:
            data["session_id"] = session_id

        req = {"type": "Request", "request_id": str(req_id), "api": api, "data": data}
        req_id += 1
        try:
            resp = _send(host, port, req)
        except Exception as e:
            print(f"error: {e}")
            continue
        print(json.dumps(resp, indent=2))
        if api == "Login" and resp.get("ok"):
            session_id = resp.get("data", {}).get("session_id")
            print(f"session_id set to {session_id}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--role", required=True, choices=["buyer", "seller"])
    args = parser.parse_args()
    repl(args.host, args.port, args.role)


if __name__ == "__main__":
    main()
