from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_route_audit_script_supports_direct_invocation(tmp_path: Path) -> None:
    input_dir = tmp_path / "converted"
    input_dir.mkdir()
    output_json = tmp_path / "route-audit.json"
    output_md = tmp_path / "route-audit.md"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "audit_routes.py"),
            "--input-dir",
            str(input_dir),
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output_json.read_text(encoding="utf-8"))["report_count"] == 0
    assert output_md.is_file()
