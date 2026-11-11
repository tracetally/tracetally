# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.support.factories import call_record, write_jsonl_records
from tracetally._cta import AUDIT_CTA_TEXT, AUDIT_CTA_URL
from tracetally.analysis.engine import cost_label, rank_findings, unknown_cost
from tracetally.cli import EXIT_OK, app
from tracetally.core.money import Range
from tracetally.core.schema import Effort, Finding, Level
from tracetally.readers.guide import no_export_text

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


def test_run_without_a_path_prints_the_export_guide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "tracetally-report"
    result = runner.invoke(app, ["run", "--out", str(out)])
    assert result.exit_code == EXIT_OK, result.output
    assert result.stdout == no_export_text() + "\n"
    assert AUDIT_CTA_URL in result.stdout
    assert result.stdout.count(AUDIT_CTA_URL) == 1
    assert "path:  tracetally run ./path" in result.stdout
    assert not out.exists()


def test_run_warns_on_a_bad_file_and_continues(tmp_path: Path) -> None:
    good = tmp_path / "good.jsonl"
    good.write_text(
        '{"model":"gpt-4o-mini","provider":"openai","start_time":"2026-10-01T00:00:00Z",'
        '"input_tokens":10,"output_tokens":2}\n',
        encoding="utf-8",
    )
    missing = tmp_path / "missing.jsonl"
    folder = tmp_path / "exports"
    write_jsonl_records(
        folder,
        [call_record(start_time="2026-11-01T00:00:00Z", record_id="folder-call")],
        name="inside.jsonl",
    )
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        ["run", str(missing), str(folder), str(good), "--offline", "--out", str(out)],
    )
    assert result.exit_code == EXIT_OK, result.output
    warnings = result.stderr or result.output
    assert "file skipped" in warnings
    assert "missing.jsonl" in warnings
    assert "not a file" not in warnings
    payload = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert payload["totals"]["calls"] == 2
    assert any("file skipped" in warning for warning in payload["warnings"])
    assert not list(out.glob(".tracetally-*"))


def test_run_directory_follows_umask_and_prints_price_warnings(tmp_path: Path) -> None:
    source = tmp_path / "calls.jsonl"
    source.write_text(
        '{"model":"gpt-4o-mini","provider":"openai","start_time":"2026-10-01T00:00:00Z",'
        '"input_tokens":10,"output_tokens":1}\n',
        encoding="utf-8",
    )
    config = tmp_path / "prices.toml"
    config.write_text('surprise = "1"\ncurrency = "USD"\n', encoding="utf-8")
    out = tmp_path / "report"
    result = runner.invoke(
        app,
        ["run", str(source), "--offline", "--pricing-config", str(config), "--out", str(out)],
    )
    assert result.exit_code == EXIT_OK, result.output
    warnings = result.stderr or result.output
    assert "unknown key" in warnings
    assert "surprise" in warnings
    assert out.is_dir()
    if os.name == "posix":
        current = os.umask(0)
        os.umask(current)
        assert (out.stat().st_mode & 0o777) == (0o755 & ~current & 0o777)


def test_run_strips_terminal_controls_from_names(tmp_path: Path) -> None:
    model = "gpt-4o-mini\x1b[31mRED"
    source = tmp_path / "calls.jsonl"
    source.write_text(
        json.dumps(
            {
                "model": model,
                "provider": "openai",
                "endpoint": "edge\x1b[2J",
                "start_time": "2026-10-01T00:00:00Z",
                "input_tokens": 10,
                "output_tokens": 1,
                "http_status": 500,
                "duration_ms": 20,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    result = runner.invoke(app, ["run", str(source), "--offline", "--out", str(out)])
    assert result.exit_code == EXIT_OK, result.output
    assert "\x1b" not in result.output
    assert "\x1b" not in (result.stderr or "")


def test_run_directory_is_scanned(tmp_path: Path) -> None:
    exports = tmp_path / "exports"
    write_jsonl_records(exports, [call_record()])
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        ["run", str(exports), "--offline", "--out", str(out), "--no-open"],
    )
    assert result.exit_code == EXIT_OK, result.output
    assert "JSONL" in result.stdout
    assert "calls.jsonl" in result.stdout
    payload = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert payload["totals"]["calls"] == 1
    assert payload["records_dropped"] == 0
    assert (out / "dashboard.html").is_file()


def test_run_rejects_a_symlink_root_and_ancestor(tmp_path: Path) -> None:
    real = tmp_path / "real"
    write_jsonl_records(real / "child", [call_record(record_id="secret-link")], name="secret.jsonl")
    try:
        link_root = tmp_path / "link-root"
        link_root.symlink_to(real / "child", target_is_directory=True)
        linkparent = tmp_path / "linkparent"
        linkparent.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available")
    out = tmp_path / "out"
    rooted = runner.invoke(
        app, ["run", str(link_root), "--offline", "--out", str(out), "--no-open"]
    )
    assert rooted.exit_code == EXIT_OK, rooted.output
    assert "No LLM usage file found" in rooted.stdout
    assert "secret" not in rooted.stdout
    assert not out.exists()
    nested = runner.invoke(
        app,
        ["run", str(linkparent / "child"), "--offline", "--out", str(out), "--no-open"],
    )
    assert nested.exit_code == EXIT_OK, nested.output
    assert "No LLM usage file found" in nested.stdout
    assert not out.exists()


def test_run_does_not_read_an_explicit_symlink_file(tmp_path: Path) -> None:
    real = tmp_path / "real"
    write_jsonl_records(
        real / "nested",
        [call_record(record_id="through-ancestor")],
        name="secret.jsonl",
    )
    target = write_jsonl_records(
        real, [call_record(record_id="through-file-link")], name="plain.jsonl"
    )
    ordinary = write_jsonl_records(
        tmp_path, [call_record(record_id="ordinary")], name="ordinary.jsonl"
    )
    try:
        alias = tmp_path / "alias.jsonl"
        alias.symlink_to(target)
        linkparent = tmp_path / "linkparent"
        linkparent.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available")
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "run",
            str(alias),
            str(linkparent / "nested" / "secret.jsonl"),
            str(ordinary),
            "--offline",
            "--out",
            str(out),
            "--no-open",
        ],
    )
    assert result.exit_code == EXIT_OK, result.output
    assert "alias.jsonl: symlink" in result.stdout
    assert "linkparent: symlink" in result.stdout
    payload = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert payload["totals"]["calls"] == 1
    assert payload["inputs"][0]["records"] == 1
    joined = " ".join(item["path"] for item in payload["inputs"])
    assert "ordinary.jsonl" in joined
    assert "secret.jsonl" not in joined
    assert "alias.jsonl" not in joined
    assert "plain.jsonl" not in joined


def test_empty_directory_argument_prints_the_guide(tmp_path: Path) -> None:
    empty = tmp_path / "exports"
    empty.mkdir()
    out = tmp_path / "out"
    result = runner.invoke(app, ["run", str(empty), "--out", str(out)])
    assert result.exit_code == EXIT_OK, result.output
    assert result.stdout == no_export_text() + "\n"
    assert not out.exists()


def test_dashboard_opens_with_a_file_uri(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tracetally.cli._dashboard_should_open", lambda *, no_open: not no_open)
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)
    source = write_jsonl_records(tmp_path, [call_record()])
    out = tmp_path / "my reports" / "ダッシュ"
    result = runner.invoke(app, ["run", str(source), "--offline", "--out", str(out)])
    assert result.exit_code == EXIT_OK, result.output
    expected = (out / "dashboard.html").resolve().as_uri()
    assert opened == [expected]
    assert expected.startswith("file:")
    assert " " not in expected
    assert "ダッシュ" not in expected or "%" in expected


def test_no_open_and_a_failed_open_still_exit_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tracetally.cli._dashboard_should_open", lambda *, no_open: not no_open)
    source = write_jsonl_records(tmp_path, [call_record()])
    out = tmp_path / "closed"
    skipped = runner.invoke(app, ["run", str(source), "--offline", "--out", str(out), "--no-open"])
    assert skipped.exit_code == EXIT_OK, skipped.output
    assert (out / "dashboard.html").is_file()

    def refuse(_url: str) -> bool:
        return False

    monkeypatch.setattr("webbrowser.open", refuse)
    again = tmp_path / "again"
    failed = runner.invoke(app, ["run", str(source), "--offline", "--out", str(again)])
    assert failed.exit_code == EXIT_OK, failed.output
    dashboard = again / "dashboard.html"
    assert f"dashboard: {dashboard.resolve()}" in failed.stdout

    def explode(_url: str) -> bool:
        raise OSError("no display")

    monkeypatch.setattr("webbrowser.open", explode)
    third = tmp_path / "third"
    raised = runner.invoke(app, ["run", str(source), "--offline", "--out", str(third)])
    assert raised.exit_code == EXIT_OK, raised.output
    assert f"dashboard: {(third / 'dashboard.html').resolve()}" in raised.stdout


def test_open_is_skipped_without_a_tty_or_under_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    from tracetally.cli import _dashboard_should_open

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert _dashboard_should_open(no_open=False) is True
    assert _dashboard_should_open(no_open=True) is False
    monkeypatch.setenv("CI", "true")
    assert _dashboard_should_open(no_open=False) is False
    monkeypatch.delenv("CI")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert _dashboard_should_open(no_open=False) is False


def _prices(directory: Path) -> Path:
    path = directory / "tracetally.toml"
    path.write_text('currency = "EUR"\nfx_rate = "0.9"\n', encoding="utf-8")
    return path
