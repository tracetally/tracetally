# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from tracetally._cta import AUDIT_CTA_TEXT, AUDIT_CTA_URL
from tracetally.analysis.engine import cost_label, rank_findings, unknown_cost
from tracetally.cli import EXIT_OK, EXIT_USAGE, app
from tracetally.core.money import Range
from tracetally.core.schema import Effort, Finding, Level

runner = CliRunner()


def _finding(check: str, title: str, mid: str, *, what_if: bool = False) -> Finding:
    amount = Decimal(mid)
    return Finding(
        id=f"{check}:{title}",
        check=check,
        title=title,
        severity=Level.LOW,
        est_monthly_usd=Range(low=amount, mid=amount, high=amount),
        effort=Effort.S,
        quality_risk=Level.LOW,
        confidence=Level.MED,
        recommendation="Do the thing.",
        what_if=what_if,
    )


def test_unknown_cost_sorts_after_estimated_and_reliability() -> None:
    priced = _finding("token_bloat", "big", "3")
    smaller = _finding("cache_opportunity", "small", "1")
    reliability = _finding("latency_tail", "slow", "0")
    unknown = _finding("retry_waste", "unpriced", "0")
    ranked = rank_findings([unknown, reliability, smaller, priced])
    assert [item.title for item in ranked] == ["big", "small", "slow", "unpriced"]
    assert unknown_cost(unknown)
    assert not unknown_cost(reliability)
    assert cost_label(unknown, "USD") == "unknown cost"
    assert cost_label(reliability, "USD") == "no dollar estimate"


def test_run_writes_the_report_and_ranks_findings(tmp_path: Path) -> None:
    from tests.support.synthetic import write_examples

    write_examples(tmp_path)
    out = tmp_path / "report"
    result = runner.invoke(
        app,
        [
            "run",
            str(tmp_path / "synthetic.jsonl"),
            "--offline",
            "--out",
            str(out),
            "--pricing-config",
            str(_prices(tmp_path)),
        ],
    )
    assert result.exit_code == EXIT_OK, result.output
    assert (out / "findings.json").is_file()
    assert (out / "report.md").is_file()
    assert (out / "dashboard.html").is_file()
    assert not (out / "report.html").exists()
    assert f"report: {out}" in result.output
    assert result.output.rstrip().endswith(f"{AUDIT_CTA_TEXT}: {AUDIT_CTA_URL}")
    markdown = (out / "report.md").read_text(encoding="utf-8")
    assert markdown.rstrip().endswith(f"[{AUDIT_CTA_TEXT}]({AUDIT_CTA_URL})")
    payload = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["price_source"]
    assert payload["price_url"].startswith("https://")
    assert payload["price_table_retrieved_on"]
    assert len(payload["price_sha256"]) == 64
    assert payload["currency"] == "EUR"
    assert payload["fx_rate"] == "0.9"
    assert payload["cta_text"] == AUDIT_CTA_TEXT
    assert payload["cta_url"] == AUDIT_CTA_URL
    seen_unknown = False
    known: list[Decimal] = []
    reliability = {"latency_tail", "error_rates", "observability_gap"}
    for item in payload["findings"]:
        if item["cost_label"] == "unknown cost":
            seen_unknown = True
            continue
        if item["check"] in reliability:
            assert not seen_unknown
            continue
        assert not seen_unknown
        known.append(Decimal(item["est_monthly_usd"]["mid"]))
    assert known == sorted(known, reverse=True)
    assert "Book a detailed audit" in (out / "dashboard.html").read_text(encoding="utf-8")


def test_run_without_a_path_points_at_the_usage() -> None:
    result = runner.invoke(app, ["run"])
    assert result.exit_code == EXIT_USAGE
    text = result.stderr or result.output
    assert "needs a trace file" in text
    assert "tracetally run [PATH ...]" in text
    assert "Auto-detect" in text


def test_run_warns_on_a_bad_file_and_continues(tmp_path: Path) -> None:
    good = tmp_path / "good.jsonl"
    good.write_text(
        '{"model":"gpt-4o-mini","provider":"openai","start_time":"2026-10-01T00:00:00Z",'
        '"input_tokens":10,"output_tokens":2}\n',
        encoding="utf-8",
    )
    missing = tmp_path / "missing.jsonl"
    folder = tmp_path / "not-a-file"
    folder.mkdir()
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        ["run", str(missing), str(folder), str(good), "--offline", "--out", str(out)],
    )
    assert result.exit_code == EXIT_OK, result.output
    warnings = result.stderr or result.output
    assert "file skipped" in warnings
    assert "missing.jsonl" in warnings
    assert "not a file" in warnings
    payload = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert payload["totals"]["calls"] == 1
    assert any("file skipped" in warning for warning in payload["warnings"])
    assert not list(out.glob(".tracetally-*"))


def _prices(directory: Path) -> Path:
    path = directory / "tracetally.toml"
    path.write_text('currency = "EUR"\nfx_rate = "0.9"\n', encoding="utf-8")
    return path
