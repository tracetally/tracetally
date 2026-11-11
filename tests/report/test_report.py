# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from tests.report.test_html_offline import assert_no_external_loads
from tests.support.factories import write_openai_usage_csv
from tests.support.synthetic import CANARY, write_examples
from tracetally.cli import EXIT_INPUT, EXIT_UNPRICED, EXIT_USAGE, app
from tracetally.core.schema import RunResult
from tracetally.report.links import EVALS_URL, PROJECT_URL
from tracetally.report.svg import bar_chart

runner = CliRunner()
_FROZEN = datetime(2026, 11, 2, 4, 31, tzinfo=timezone.utc)


def test_svg_chart_has_no_links() -> None:
    from decimal import Decimal

    chart = bar_chart("Cost by model", [("openai/gpt-4o-mini", Decimal("1.5"))])
    assert "<svg" in chart
    assert "href=" not in chart.lower()
    assert "<script" not in chart.lower()
    assert "1.500000" in chart


def test_report_writes_three_files_and_stays_offline(tmp_path: Path) -> None:
    write_examples(tmp_path)
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "report",
            str(tmp_path / "synthetic.jsonl"),
            "--tier-map",
            str(tmp_path / "tiers.example.yaml"),
            "--out",
            str(out),
            "--offline",
        ],
    )
    assert result.exit_code == 0, result.output
    html = (out / "report.html").read_text(encoding="utf-8")
    markdown = (out / "report.md").read_text(encoding="utf-8")
    payload = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert_no_external_loads(html)
    assert html.count("<svg") >= 3
    assert "Cost by model" in html
    assert "Latency p50" in html
    assert "p50" in html
    assert PROJECT_URL in html
    assert EVALS_URL in html
    assert "validate with evals" in html
    assert "human-reviewed" in html
    assert CANARY not in html
    assert CANARY not in markdown
    assert CANARY not in json.dumps(payload)
    assert payload["schema_version"] == 1
    assert "synthetic" not in payload
    assert "demo_seed" not in payload
    assert payload["include_content"] is False
    assert payload["content_excerpts"] == []
    assert payload["totals"]["est_monthly_savings_usd"]["mid"] == "0.02379"
    assert payload["totals"]["est_monthly_what_if_usd"]["mid"] == "0.0846"
    assert "0.011250" in html
    assert "0.023790" in html
    checks = [item["check"] for item in payload["findings"]]
    assert checks.index("model_swap") < checks.index("token_bloat")
    assert html.index("model_swap") < html.index("token_bloat")
    RunResult.model_validate(payload)
    assert "By model" in result.output
    assert "Findings" in result.output
    assert "wrote" in result.output
    finding_ids = " ".join(item["id"] for item in payload["findings"])
    assert "support.control" not in finding_ids


def test_no_footer_drops_the_project_link(tmp_path: Path) -> None:
    write_examples(tmp_path)
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "report",
            str(tmp_path / "synthetic.jsonl"),
            "--out",
            str(out),
            "--no-footer",
            "--offline",
        ],
    )
    assert result.exit_code == 0, result.output
    html = (out / "report.html").read_text(encoding="utf-8")
    markdown = (out / "report.md").read_text(encoding="utf-8")
    assert_no_external_loads(html)
    assert f'href="{PROJECT_URL}"' not in html
    assert f"]({PROJECT_URL})" not in markdown
    assert "human-reviewed" not in html.lower()
    assert EVALS_URL in html
    assert json.loads((out / "findings.json").read_text(encoding="utf-8"))["show_footer"] is False


def test_include_content_keeps_the_canary_and_redact_removes_it(tmp_path: Path) -> None:
    write_examples(tmp_path)
    source = tmp_path / "synthetic.jsonl"
    tiers = tmp_path / "tiers.example.yaml"
    shown = tmp_path / "shown"
    hidden = tmp_path / "hidden"
    redacted = tmp_path / "redacted"
    included = runner.invoke(
        app,
        [
            "report",
            str(source),
            "--tier-map",
            str(tiers),
            "--out",
            str(shown),
            "--include-content",
            "--offline",
        ],
    )
    assert included.exit_code == 0, included.output
    html = (shown / "report.html").read_text(encoding="utf-8")
    body = (shown / "findings.json").read_text(encoding="utf-8")
    assert CANARY in html
    assert CANARY in body
    assert_no_external_loads(html)
    assert "&lt;" not in CANARY

    plain = runner.invoke(app, ["report", str(source), "--out", str(hidden), "--offline"])
    assert plain.exit_code == 0, plain.output
    assert CANARY not in (hidden / "report.html").read_text(encoding="utf-8")

    both = runner.invoke(
        app,
        [
            "report",
            str(source),
            "--out",
            str(redacted),
            "--include-content",
            "--redact",
            "--offline",
        ],
    )
    assert both.exit_code == 0, both.output
    assert "not applied" in (both.stderr or both.output)
    redacted_html = (redacted / "report.html").read_text(encoding="utf-8")
    redacted_json = (redacted / "findings.json").read_text(encoding="utf-8")
    assert CANARY not in redacted_html
    assert CANARY not in redacted_json
    assert "trace-bloat-low-0" not in redacted_html
    assert "trace-agent-loop" not in redacted_json
    assert "redacted:" in redacted_html
    assert str(source) not in redacted_html
    assert_no_external_loads(redacted_html)
    payload = json.loads(redacted_json)
    assert payload["include_content"] is False
    assert payload["redacted"] is True


def test_hostile_markup_is_text(tmp_path: Path) -> None:
    path = tmp_path / "hostile.jsonl"
    endpoint = '<img src="https://evil.example/pixel.png"><script>alert(1)</script>'
    path.write_text(
        json.dumps(
            {
                "model": "gpt-4o-mini",
                "provider": "openai",
                "endpoint": endpoint,
                "start_time": "2026-10-01T00:00:00Z",
                "input_tokens": 10,
                "output_tokens": 2,
                "duration_ms": 15,
                "prompt": "<script>alert(1)</script>",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        ["report", str(path), "--out", str(out), "--include-content", "--offline"],
    )
    assert result.exit_code == 0, result.output
    html = (out / "report.html").read_text(encoding="utf-8")
    assert_no_external_loads(html)
    assert "<script" not in html.lower()
    assert "<img" not in html.lower()
    assert "evil.example" in html
    assert "&lt;script&gt;" in html


def test_cost_only_csv_skips_trace_checks(tmp_path: Path) -> None:
    csv_path = write_openai_usage_csv(tmp_path)
    out = tmp_path / "out"
    result = runner.invoke(app, ["report", str(csv_path), "--out", str(out), "--offline"])
    assert result.exit_code == 0, result.output
    html = (out / "report.html").read_text(encoding="utf-8")
    payload = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert_no_external_loads(html)
    assert "Cost-only" in html
    skipped = {item["check"] for item in payload["skipped_checks"]}
    assert "cache_opportunity" in skipped
    assert "agent_loops" in skipped
    assert any(item["check"] == "observability_gap" for item in payload["findings"])
    assert "not measured" in html.lower() or "Latency was not measured" in html


def test_report_exit_codes(tmp_path: Path) -> None:
    missing = runner.invoke(
        app, ["report", str(tmp_path / "missing.jsonl"), "--out", str(tmp_path / "a")]
    )
    assert missing.exit_code == EXIT_INPUT

    unknown = tmp_path / "unknown.jsonl"
    unknown.write_text(
        '{"model":"no-such-model","provider":"openai","start_time":"2026-10-01T00:00:00Z",'
        '"input_tokens":1,"output_tokens":1}\n',
        encoding="utf-8",
    )
    unpriced = runner.invoke(
        app, ["report", str(unknown), "--out", str(tmp_path / "b"), "--offline"]
    )
    assert unpriced.exit_code == EXIT_UNPRICED
    assert (tmp_path / "b" / "findings.json").is_file()

    blocked = tmp_path / "not-a-dir"
    blocked.write_text("x", encoding="utf-8")
    bad_out = runner.invoke(app, ["report", str(unknown), "--out", str(blocked), "--offline"])
    assert bad_out.exit_code == EXIT_USAGE


def test_frozen_generated_at_is_the_only_clock(tmp_path: Path) -> None:
    """Direct renderer call: the stamped time is the one the caller passed."""
    from tracetally.analysis.engine import analyze
    from tracetally.config import ReaderConfig
    from tracetally.pricing.table import load_bundled
    from tracetally.readers.base import load_paths
    from tracetally.report.render import ReportOptions, write_report

    write_examples(tmp_path)
    store = load_paths([tmp_path / "synthetic.jsonl"], ReaderConfig())
    result = analyze(store, load_bundled())
    written = write_report(
        store,
        result,
        ReportOptions(out=tmp_path / "out", generated_at=_FROZEN),
    )
    html = written.html.read_text(encoding="utf-8")
    assert "2026-11-02T04:31:00+00:00" in html
    assert_no_external_loads(html)
