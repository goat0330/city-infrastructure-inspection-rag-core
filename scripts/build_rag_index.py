"""Build a lightweight JSONL and NumPy RAG index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rag import build_index  # noqa: E402


def _read_entries(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at line {line_number}: {path}") from exc
            if not isinstance(entry, dict):
                raise ValueError(f"entry at line {line_number} is not a JSON object: {path}")
            entries.append(entry)
    return entries


def _client_from_environment() -> Any:
    from src.llm.client import OpenAIModelClient

    factory = getattr(OpenAIModelClient, "from_env", None)
    if callable(factory):
        return factory()
    return OpenAIModelClient()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--exclude-sample-id")
    parser.add_argument("--fit-only", action="store_true")
    args = parser.parse_args(argv)

    entries = _read_entries(args.entries_jsonl)
    client = _client_from_environment()
    index = build_index(
        entries,
        args.output_dir,
        client,
        exclude_sample_id=args.exclude_sample_id,
        fit_only=args.fit_only,
    )
    print(
        json.dumps(
            {
                "metadata": "metadata.jsonl",
                "vectors": "vectors.npy",
                "entries": len(index.metadata),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
