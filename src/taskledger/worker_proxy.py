from __future__ import annotations

import argparse
import json
import sys

from .controller.worker_broker import request
from .core import MAX_JSON, canonical


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("action", choices=("context", "check", "checkpoint", "artifact-register", "question", "blocker", "follow-up", "submit"))
    parser.add_argument("--socket", required=True)
    parser.add_argument("--input", default="-")
    args = parser.parse_args(argv)
    raw = sys.stdin.buffer.read(MAX_JSON + 1) if args.input == "-" else open(args.input, "rb").read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        sys.stdout.write(canonical({"ok": False, "error": {"code": "INVALID_REQUEST", "message": "Input is too large."}}) + "\n")
        return 2
    try:
        data = json.loads(raw or b"{}")
        result = request(args.socket, args.action, data)
    except Exception as exc:
        result = {"ok": False, "error": {"code": "BROKER_UNAVAILABLE", "message": str(exc)}}
    sys.stdout.write(canonical(result) + "\n")
    return 0 if result.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(main())
