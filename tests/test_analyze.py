# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.support.synthetic import (
    CANARY,
    CONTROL_ENDPOINT,
    PLANTED,
    write_examples,
)
from tracetally.analysis.engine import analyze
from tracetally.cli import EXIT_INPUT, EXIT_UNPRICED, EXIT_USAGE, app
from tracetally.config import (
    AnalyzeConfig,
    ReaderConfig,
    TierTarget,
    load_check_params,
    load_tier_map,
)
from tracetally.core.errors import UsageError
from tracetally.core.schema import Capability, LLMCall, Status
from tracetally.core.store import TraceStore
from tracetally.pricing.table import load_bundled
from tracetally.readers.base import load_paths

runner = CliRunner()
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_TRACE_CHECKS = (
    "token_bloat",
    "cache_opportunity",
    "batch_candidates",
    "retry_waste",
    "agent_loops",
    "model_swap",
    "latency_tail",
    "error_rates",
)
_WHAT_IF = "what-if: price only, validate quality with evals"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _within_ten_percent(actual: Decimal, expected: str) -> None:
    target = Decimal(expected)
    assert abs(actual - target) <= abs(target) * Decimal("0.10")


def _load(path: Path, tiers: Path) -> tuple[TraceStore, AnalyzeConfig]:
    store = load_paths([path], ReaderConfig())
    return store, AnalyzeConfig(tier_map=load_tier_map(tiers))


def test_planted_issues_match_and_control_is_clean(tmp_path: Path) -> None:
    write_examples(tmp_path)
    table = load_bundled()
    tiers = tmp_path / "tiers.example.yaml"
    for name in ("synthetic.jsonl", "synthetic_otel.json"):
        store, cfg = _load(tmp_path / name, tiers)
        result = analyze(store, table, cfg)
        by_check = {item.check: item for item in result.findings if item.check in PLANTED}
        assert set(by_check) == set(PLANTED)
        for check, expected in PLANTED.items():
            finding = by_check[check]
            assert finding.evidence[0].endpoint == expected["endpoint"]
            assert finding.what_if is expected["what_if"]
            _within_ten_percent(finding.est_monthly_usd.low, expected["low"])
            _within_ten_percent(finding.est_monthly_usd.mid, expected["mid"])
            _within_ten_percent(finding.est_monthly_usd.high, expected["high"])
            assert finding.est_monthly_usd.low == Decimal(expected["low"])
            assert finding.est_monthly_usd.mid == Decimal(expected["mid"])
            assert finding.est_monthly_usd.high == Decimal(expected["high"])
        for finding in result.findings:
            assert finding.evidence[0].endpoint != CONTROL_ENDPOINT
            assert CONTROL_ENDPOINT not in finding.id
        assert result.skipped == []
        assert result.monthly_factor == Decimal(1)
        text = result.to_text()
        payload = result.to_json()
        assert CANARY not in text
        assert CANARY not in payload
        assert _WHAT_IF in text
        assert "No telemetry." in text
        document = json.loads(payload)
        assert document["analyze_schema_version"] == 1
        assert document["totals"]["est_monthly_savings_usd"]["mid"] == "0.02379"
        assert document["totals"]["est_monthly_what_if_usd"]["mid"] == "0.0846"
        swap = next(item for item in document["findings"] if item["check"] == "model_swap")
        assert swap["what_if"] is True
        assert _WHAT_IF in swap["recommendation"]
        assert all(
            item["what_if"] is False
            for item in document["findings"]
            if item["check"] != "model_swap"
        )
        assert {item.check for item in result.savings()} == set(PLANTED) - {"model_swap"}


def test_checks_skip_when_capabilities_or_tier_map_are_missing() -> None:
    store = TraceStore()
    store.capabilities = frozenset({Capability.PER_CALL})
    store.append_call(_llm())
    result = analyze(store, load_bundled(), AnalyzeConfig())
    reasons = {item.check: item.reason for item in result.skipped}
    assert "prompt_hashes" in reasons["cache_opportunity"]
    assert "errors" in reasons["retry_waste"]
    assert "trace_tree" in reasons["agent_loops"]
    assert "latency" in reasons["latency_tail"]
    assert "errors" in reasons["error_rates"]
    assert "what-if" in reasons["model_swap"]
    assert "--tier-map" in reasons["model_swap"]
    assert "observability_gap" not in reasons
    assert any(item.check == "observability_gap" for item in result.findings)

    selected = analyze(
        store,
        load_bundled(),
        AnalyzeConfig(only=frozenset({"token_bloat"}), tier_map={"ep": _target()}),
    )
    assert {item.check for item in selected.skipped if item.reason == "not selected by --only"} >= {
        "cache_opportunity",
        "model_swap",
    }
    with pytest.raises(UsageError, match="unknown check"):
        analyze(store, load_bundled(), AnalyzeConfig(skip=frozenset({"not-a-check"})))


_EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
_CANARIES = (
    "CANARY_PROMPT_DO_NOT_LEAK",
    "CANARY_OUTPUT_DO_NOT_LEAK",
    "CANARY_TOOL",
    "CANARY_META",
)
_MISSING_CAPABILITY = {
    "token_bloat": "per_call",
    "cache_opportunity": "prompt_hashes",
    "batch_candidates": "per_call",
    "retry_waste": "errors",
    "agent_loops": "trace_tree",
    "model_swap": "per_call",
    "latency_tail": "latency",
    "error_rates": "errors",
}


def test_capability_skips_use_the_langfuse_and_openai_csv_readers() -> None:
    """Skips come from the real readers, not a hand-built capability set."""
    table = load_bundled()
    usage = _FIXTURES / "openai" / "usage.csv"
    observations = _FIXTURES / "langfuse" / "observations.json"

    csv_store = load_paths([usage], ReaderConfig())
    assert csv_store.readers == {"openai_csv"}
    assert csv_store.capabilities == frozenset()
    csv_result = analyze(csv_store, table, AnalyzeConfig())
    csv_reasons = {item.check: item.reason for item in csv_result.skipped}
    assert set(csv_reasons) == set(_TRACE_CHECKS)
    for check, reason in csv_reasons.items():
        assert "the OpenAI usage CSV is cost-only and does not include" in reason
        assert _MISSING_CAPABILITY[check] in reason
    assert "--tier-map" not in csv_reasons["model_swap"]
    assert {item.check for item in csv_result.findings} == {"observability_gap"}

    lang_store = load_paths([observations], ReaderConfig())
    assert lang_store.readers == {"langfuse"}
    assert lang_store.capabilities == frozenset(Capability)
    lang_result = analyze(lang_store, table, AnalyzeConfig())
    assert {item.check for item in lang_result.skipped} == {"model_swap"}
    swap_reason = lang_result.skipped[0].reason
    assert "--tier-map" in swap_reason
    assert "what-if" in swap_reason
    rendered = lang_result.to_text() + lang_result.to_json()
    for canary in _CANARIES:
        assert canary not in rendered

    mixed = load_paths([usage, observations], ReaderConfig())
    assert mixed.readers == {"openai_csv", "langfuse"}
    assert mixed.capabilities == frozenset()
    mixed_result = analyze(mixed, table, AnalyzeConfig())
    agent = next(item for item in mixed_result.skipped if item.check == "agent_loops")
    assert "an input is cost-only and does not include" in agent.reason
    assert "trace_tree" in agent.reason

    example_csv = analyze(
        load_paths([_EXAMPLES / "synthetic_openai_usage.csv"], ReaderConfig()),
        table,
        AnalyzeConfig(),
    )
    cache_reason = next(
        item.reason for item in example_csv.skipped if item.check == "cache_opportunity"
    )
    assert "OpenAI usage CSV is cost-only" in cache_reason
    example_lang = analyze(
        load_paths([_EXAMPLES / "synthetic_langfuse.json"], ReaderConfig()),
        table,
        AnalyzeConfig(),
    )
    assert {item.check for item in example_lang.skipped} == {"model_swap"}

    cli = runner.invoke(app, ["analyze", str(usage), "--json"])
    assert cli.exit_code == 0, cli.output
    payload = json.loads(cli.stdout or cli.output)
    skipped = {item["check"]: item["reason"] for item in payload["skipped_checks"]}
    assert set(skipped) == set(_TRACE_CHECKS)
    assert "cost-only" in skipped["latency_tail"]
    assert {item["check"] for item in payload["findings"]} == {"observability_gap"}


def test_batch_savings_do_not_halve_cached_gemini_tokens() -> None:
    """Gemini 2.5 batch cache stays at the standard cache price.

    1M input / 500k cached flash: sync 0.165, batch 0.09, delta 0.075.
    Halving the whole sync cost would be 0.0825 and is not the published price.
    A fully cached call has no batch savings.
    """
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    end = datetime(2026, 10, 31, tzinfo=timezone.utc)
    mixed = _store(
        [
            _llm(
                record_id="a",
                trace_id="ta",
                endpoint="nightly.flash",
                provider="gemini",
                model="gemini-2.5-flash",
                start_time=start,
                input_tokens=1_000_000,
                cached_input_tokens=500_000,
                output_tokens=0,
                interactive=False,
            ),
            _llm(
                record_id="b",
                trace_id="tb",
                endpoint="nightly.flash",
                provider="gemini",
                model="gemini-2.5-flash",
                start_time=end,
                input_tokens=1_000_000,
                cached_input_tokens=500_000,
                output_tokens=0,
                interactive=False,
            ),
        ]
    )
    result = analyze(mixed, load_bundled(), AnalyzeConfig())
    finding = next(item for item in result.findings if item.check == "batch_candidates")
    assert finding.est_monthly_usd.mid == Decimal("0.1125")
    assert finding.est_monthly_usd.high == Decimal("0.15")
    assert "standard" in finding.caveats[0]

    cached = _store(
        [
            _llm(
                record_id="c",
                trace_id="tc",
                endpoint="nightly.cached",
                provider="gemini",
                model="gemini-2.5-flash",
                start_time=start,
                input_tokens=1_000_000,
                cached_input_tokens=1_000_000,
                output_tokens=0,
                interactive=False,
            )
        ]
    )
    quiet = analyze(cached, load_bundled(), AnalyzeConfig())
    assert not any(item.check == "batch_candidates" for item in quiet.findings)


def test_missing_cached_price_is_not_invented() -> None:
    digest = "a" * 32
    prefixes = ((1024, "b" * 32), (2048, "c" * 32))
    store = _store(
        [
            _llm(
                record_id=str(index),
                trace_id=f"t{index}",
                endpoint="legacy",
                model="gpt-4o-2024-05-13",
                input_tokens=3000,
                output_tokens=10,
                content_hash=digest,
                prefix_hashes=prefixes,
                user_id=f"u{index}",
                session_id=f"s{index}",
                interactive=True,
            )
            for index in range(20)
        ]
    )
    result = analyze(store, load_bundled(), AnalyzeConfig())
    assert not any(item.check == "cache_opportunity" for item in result.findings)


def test_short_window_latency_and_config_errors(tmp_path: Path) -> None:
    start = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    calls = []
    for index in range(20):
        calls.append(
            _llm(
                record_id=str(index),
                trace_id=f"slow-{index}",
                endpoint="slow",
                start_time=start + timedelta(seconds=index),
                duration_ms=100.0 if index < 19 else 10_000.0,
                user_id=f"u{index}",
                session_id=f"s{index}",
                interactive=True,
            )
        )
    result = analyze(_store(calls), load_bundled(), AnalyzeConfig())
    tail = next(item for item in result.findings if item.check == "latency_tail")
    assert tail.est_monthly_usd.mid == 0
    assert tail.evidence[0].endpoint == "slow"
    assert any("under 1 day" in caveat for caveat in tail.caveats)

    good = tmp_path / "ok.yaml"
    good.write_text("token_bloat:\n  min_calls: 50\n", encoding="utf-8")
    params = load_check_params(good)
    assert params.bloat_min_calls == 50
    bad = tmp_path / "bad.yaml"
    bad.write_text("not_a_check:\n  min_calls: 1\n", encoding="utf-8")
    with pytest.raises(UsageError, match="unknown config key"):
        load_check_params(bad)
    empty = tmp_path / "empty.yaml"
    empty.write_text("endpoints: {}\n", encoding="utf-8")
    with pytest.raises(UsageError, match="tier map"):
        load_tier_map(empty)


def test_analyze_command_json_skips_and_exit_codes(tmp_path: Path) -> None:
    write_examples(tmp_path)
    jsonl = tmp_path / "synthetic.jsonl"
    tiers = tmp_path / "tiers.example.yaml"
    text = runner.invoke(app, ["analyze", str(jsonl), "--tier-map", str(tiers)])
    assert text.exit_code == 0, text.output
    assert "what-if" in text.output
    assert _WHAT_IF in text.output
    assert CANARY not in text.output
    assert "support.control" not in text.output

    machine = runner.invoke(
        app, ["analyze", str(jsonl), "--tier-map", str(tiers), "--json", "--skip", "token_bloat"]
    )
    assert machine.exit_code == 0, machine.output
    payload = json.loads(machine.stdout or machine.output)
    assert payload["totals"]["observed_cost_usd"] == "0.29241"
    assert not any(item["check"] == "token_bloat" for item in payload["findings"])
    assert any(
        item["check"] == "token_bloat" and item["reason"] == "skipped with --skip"
        for item in payload["skipped_checks"]
    )

    unknown = runner.invoke(app, ["analyze", str(jsonl), "--only", "nope"])
    assert unknown.exit_code == EXIT_USAGE
    assert "unknown check" in (unknown.stderr or unknown.output)

    spans_only = tmp_path / "spans.jsonl"
    spans_only.write_text(
        '{"kind":"tool","name":"search","span_id":"s","start_time":"2026-10-01T00:00:00Z"}\n',
        encoding="utf-8",
    )
    empty = runner.invoke(app, ["analyze", str(spans_only), "--format", "jsonl"])
    assert empty.exit_code == EXIT_INPUT

    unpriced = tmp_path / "private.jsonl"
    unpriced.write_text(
        '{"model":"no-such-model","provider":"openai","start_time":"2026-10-01T00:00:00Z",'
        '"input_tokens":1,"output_tokens":1}\n',
        encoding="utf-8",
    )
    bare = runner.invoke(app, ["analyze", str(unpriced), "--json"])
    assert bare.exit_code == EXIT_UNPRICED

    help_text = runner.invoke(app, ["analyze", "--help"])
    assert help_text.exit_code == 0
    plain = _ANSI.sub("", help_text.output)
    assert "--tier-map" in plain
    assert "--only" in plain


def test_one_hundred_thousand_calls_stay_within_the_smoke_budget() -> None:
    count = 100_000
    store = TraceStore()
    store.capabilities = frozenset(Capability)
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    for index in range(count):
        store.append_call(
            _llm(
                record_id=str(index),
                trace_id=f"t{index}",
                endpoint="bulk",
                user_id="user",
                session_id="session",
                interactive=True,
                start_time=start,
            )
        )
    began = time.perf_counter()
    result = analyze(store, load_bundled(), AnalyzeConfig())
    elapsed = time.perf_counter() - began
    assert result.summary.calls == count
    assert result.findings == []
    assert elapsed < 20


def _store(calls: list[LLMCall]) -> TraceStore:
    store = TraceStore()
    store.capabilities = frozenset(Capability)
    for call in calls:
        store.append_call(call)
    return store


def _target() -> TierTarget:
    return TierTarget(model="gpt-4o-mini", provider="openai")


def _llm(**overrides: object) -> LLMCall:
    fields: dict[str, object] = {
        "source": "test",
        "record_id": "r",
        "trace_id": "t",
        "parent_id": None,
        "session_id": None,
        "user_id": None,
        "endpoint": "ep",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "operation": None,
        "start_time": datetime(2026, 10, 1, tzinfo=timezone.utc),
        "duration_ms": 100.0,
        "input_tokens": 100,
        "output_tokens": 10,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "request_count": 1,
        "status": Status.OK,
        "http_status": None,
        "error_type": None,
        "attempt": None,
        "is_batch": False,
        "interactive": None,
        "prefix_hashes": (),
        "content_hash": None,
        "tool_name": None,
        "args_hash": None,
        "reported_cost_usd": None,
        "attrs": {},
    }
    fields.update(overrides)
    return LLMCall.model_construct(**fields)  # type: ignore[arg-type]
