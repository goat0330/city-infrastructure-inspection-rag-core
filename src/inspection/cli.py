from __future__ import annotations

import argparse
import json


COMMANDS = ("audit", "build-gold", "convert", "parse", "score", "predict", "render")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m inspection")
    parser.add_argument("command", choices=COMMANDS)
    args = parser.parse_args()
    print(json.dumps({"command": args.command, "status": "contract_only"}, ensure_ascii=False))
