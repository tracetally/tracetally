# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

from typer.testing import CliRunner

from tracetally.cli import app
from tracetally.report.dashboard import csp_source_hash, embed_json

runner = CliRunner()
_PAYLOAD = "</script><img src=x onerror=alert(1)>&"
_LINE_SEP = "model\u2028name\u2029tail"


def test_embed_json_escapes_html_and_line_separators() -> None:
    text = embed_json({"name": _PAYLOAD + _LINE_SEP})
    assert "</script>" not in text
    assert "<img" not in text
    assert "&" not in text
    assert "\u2028" not in text
    assert "\u2029" not in text
    assert r"\u003c/script\u003e\u003cimg" in text
    assert r"\u0026" in text
    assert r"\u2028" in text
    assert r"\u2029" in text
    restored = json.loads(text)
    assert restored["name"] == _PAYLOAD + _LINE_SEP


def test_dashboard_escapes_hostile_names_and_hashes_the_script(tmp_path: Path) -> None:
    hostile = _PAYLOAD + _LINE_SEP
    path = tmp_path / "hostile.jsonl"
    path.write_text(
        json.dumps(
            {
                "model": hostile,
                "provider": "openai",
                "endpoint": hostile,
                "start_time": "2026-10-01T00:00:00Z",
                "input_tokens": 20,
                "output_tokens": 1,
                "http_status": 500,
                "duration_ms": 10,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    result = runner.invoke(app, ["run", str(path), "--offline", "--out", str(out)])
    assert result.exit_code == 0, result.output
    html = (out / "dashboard.html").read_text(encoding="utf-8")
    assert "</script><img" not in html
    assert "<img" not in html
    assert html.count("</script>") == 2
    assert r"\u003c/script\u003e\u003cimg" in html
    assert r"\u2028" in html
    assert r"\u2029" in html
    assert "unsafe-inline" not in html
    assert "default-src 'none'" in html
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)
    assert len(scripts) == 1
    digest = base64.b64encode(hashlib.sha256(scripts[0].encode("utf-8")).digest()).decode("ascii")
    assert f"script-src 'sha256-{digest}'" in html
    assert digest == csp_source_hash(scripts[0])
    assert "textContent" in scripts[0]
    assert "innerHTML" not in scripts[0]
    payload = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    titles = " ".join(item["title"] for item in payload["findings"])
    models = " ".join(row["model"] for row in payload["summary"]["by_model"])
    assert hostile in titles
    assert hostile in models
