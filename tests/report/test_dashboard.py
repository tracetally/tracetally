# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

from typer.testing import CliRunner

from tracetally.cli import app
from tracetally.report.dashboard import csp_source_hash, embed_json, summary_lines

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


def test_dashboard_shows_cost_priced_share_and_unpriced(tmp_path: Path) -> None:
    path = tmp_path / "mixed.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "model": "gpt-4o-mini",
                        "provider": "openai",
                        "start_time": "2026-10-01T00:00:00Z",
                        "input_tokens": 1_000_000,
                        "output_tokens": 0,
                    }
                ),
                json.dumps(
                    {
                        "model": "no-such-model",
                        "provider": "openai",
                        "start_time": "2026-10-01T00:00:01Z",
                        "input_tokens": 10,
                        "output_tokens": 1,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    result = runner.invoke(app, ["run", str(path), "--offline", "--out", str(out)])
    assert result.exit_code == 0, result.output
    payload = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert payload["totals"]["priced_calls"] == 1
    assert payload["totals"]["calls"] == 2
    assert payload["totals"]["unpriced_calls"] == 1
    assert payload["totals"]["priced_share"] == "50.00%"
    assert payload["totals"]["observed_cost_usd"] == "0.15"
    html = (out / "dashboard.html").read_text(encoding="utf-8")
    assert "Total cost " in html
    assert "Priced share " in html
    assert "Spend priced " in html
    assert "Unpriced calls " in html
    assert "cost unknown" in html
    assert "0.15" in html
    assert "50.00%" in html
    lines = _summary_lines(html)
    assert lines[0].startswith("Total cost (priced calls) ")
    assert any(line.startswith("Unpriced calls 1") for line in lines)
    assert "Spend priced 0.00%" not in lines
    assert "Spend priced cost unknown" in lines


def test_dashboard_hides_unpriced_when_every_call_is_priced(tmp_path: Path) -> None:
    path = tmp_path / "priced.jsonl"
    path.write_text(
        '{"model":"gpt-4o-mini","provider":"openai","start_time":"2026-10-01T00:00:00Z",'
        '"input_tokens":1000,"output_tokens":1}\n',
        encoding="utf-8",
    )
    out = tmp_path / "out"
    result = runner.invoke(app, ["run", str(path), "--offline", "--out", str(out), "--no-open"])
    assert result.exit_code == 0, result.output
    lines = _summary_lines((out / "dashboard.html").read_text(encoding="utf-8"))
    assert lines[0].startswith("Total cost (priced calls) ")
    assert "cost unknown" not in lines[0]
    assert "Spend priced 100.00%" in lines
    assert all(not line.startswith("Unpriced calls") for line in lines)
    assert "Unpriced calls" not in (out / "dashboard.html").read_text(encoding="utf-8")


def test_dashboard_says_cost_unknown_when_nothing_is_priced(tmp_path: Path) -> None:
    path = tmp_path / "unknown.jsonl"
    path.write_text(
        '{"model":"no-such-model","provider":"openai","start_time":"2026-10-01T00:00:00Z",'
        '"input_tokens":10,"output_tokens":1}\n',
        encoding="utf-8",
    )
    out = tmp_path / "out"
    result = runner.invoke(app, ["run", str(path), "--offline", "--out", str(out), "--no-open"])
    assert result.exit_code == 0, result.output
    lines = _summary_lines((out / "dashboard.html").read_text(encoding="utf-8"))
    assert lines[0] == "Total cost (priced calls) cost unknown"
    assert "Spend priced cost unknown" in lines
    assert "Spend priced 0.00%" not in lines
    direct = summary_lines(
        {
            "currency": "USD",
            "totals": {
                "calls": 2,
                "priced_calls": 0,
                "unpriced_calls": 2,
                "priced_share": "0.00%",
                "observed_cost_usd": None,
            },
        }
    )
    assert direct[0] == "Total cost (priced calls) cost unknown"
    assert "Spend priced cost unknown" in direct
    assert any(line.startswith("Unpriced calls 2") for line in direct)


def _summary_lines(html: str) -> list[str]:
    match = re.search(r'id="tracetally-data">(.*?)</script>', html, flags=re.DOTALL)
    assert match is not None
    payload = json.loads(match.group(1))
    lines = payload["summary_lines"]
    assert isinstance(lines, list)
    return lines
