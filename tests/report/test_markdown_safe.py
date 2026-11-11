# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from tracetally.cli import app

runner = CliRunner()


def test_report_markdown_escapes_user_supplied_text(tmp_path: Path) -> None:
    img = "<img src=x onerror=alert(1)>"
    svg = "<svg onload=alert(1)>"
    endpoint = "\n# x [a](javascript:alert(1))"
    trace_id = "id`with`tick"
    path = tmp_path / "hostile.jsonl"
    rows = [
        {
            "model": img,
            "provider": "openai",
            "endpoint": endpoint,
            "trace_id": trace_id,
            "start_time": "2026-10-01T00:00:00Z",
            "input_tokens": 10,
            "output_tokens": 1,
            "http_status": 500,
            "duration_ms": 15,
        },
        {
            "model": svg,
            "provider": "openai",
            "endpoint": "plain",
            "trace_id": "other",
            "start_time": "2026-10-01T00:00:01Z",
            "input_tokens": 10,
            "output_tokens": 1,
            "duration_ms": 15,
        },
        {
            "model": "gpt-4o-mini",
            "provider": "openai",
            "endpoint": "priced",
            "start_time": "2026-10-01T00:00:02Z",
            "input_tokens": 10,
            "output_tokens": 1,
            "duration_ms": 15,
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    out = tmp_path / "out"
    result = runner.invoke(app, ["report", str(path), "--offline", "--out", str(out)])
    assert result.exit_code == 0, result.output
    markdown = (out / "report.md").read_text(encoding="utf-8")
    assert "<img" not in markdown
    assert "<svg" not in markdown
    assert "&lt;img" in markdown
    assert "&lt;svg" in markdown
    assert "id`with`tick" not in markdown
    assert "id&#96;with&#96;tick" in markdown
    assert "[a](javascript:" not in markdown
    assert "&#91;a&#93;" in markdown
    assert not re.search(r"^ {0,3}# x\b", markdown, flags=re.M)
    assert "\n# x" not in markdown
