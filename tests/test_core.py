# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tracetally.core.errors import ReaderError
from tracetally.core.hashing import content_hash, hash_content_parts, prefix_hashes
from tracetally.core.money import Range, format_usd, token_cost
from tracetally.core.schema import (
    Capability,
    Effort,
    Finding,
    Level,
    LLMCall,
    RunResult,
    Status,
)
from tracetally.core.stats import percentile_linear, percentiles_or_none, rate
from tracetally.core.store import TraceStore


def test_reader_error_names_field_and_location() -> None:
    err = ReaderError("is required", field="gen_ai.usage.input_tokens", where="spans[3]")
    text = str(err)
    assert "spans[3]" in text
    assert "gen_ai.usage.input_tokens" in text
    assert err.field == "gen_ai.usage.input_tokens"
    bare = ReaderError("no input files")
    assert str(bare) == "no input files"


def test_llmcall_ignores_unknown_fields_and_names_missing_ones() -> None:
    call = LLMCall.model_validate(
        {
            "source": "jsonl",
            "record_id": "1",
            "endpoint": "support.reply",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "start_time": "2026-10-01T00:00:00Z",
            "input_tokens": 1,
            "output_tokens": 2,
            "status": "ok",
            "not_a_field": "ignored",
        }
    )
    assert call.input_tokens == 1
    assert call.status is Status.OK
    with pytest.raises(ValidationError) as caught:
        LLMCall.model_validate({"source": "jsonl", "model": "gpt-4o-mini"})
    assert "input_tokens" in str(caught.value)


def test_schema_exports_finding_contract() -> None:
    finding = Finding(
        id="cache_opportunity:1",
        check="cache_opportunity",
        title="Repeated prefix",
        severity=Level.MED,
        est_monthly_usd=Range(low=Decimal("1"), mid=Decimal("2"), high=Decimal("3")),
        effort=Effort.S,
        quality_risk=Level.LOW,
        confidence=Level.MED,
        recommendation="Cache the prefix.",
    )
    result = RunResult(
        tracetally_version="0.1.0.dev0",
        findings=[finding],
        price_table_id="2026-10",
        price_table_retrieved_on="2026-10-15",
    )
    assert result.schema_version == 1
    assert Capability.LATENCY.value == "latency"
    schema = LLMCall.model_json_schema()
    assert "input_tokens" in schema["required"]
    assert "model" in schema["required"]


def test_range_rejects_unordered_values() -> None:
    with pytest.raises(ValidationError):
        Range(low=Decimal("3"), mid=Decimal("1"), high=Decimal("2"))


def test_token_cost_uses_decimal() -> None:
    assert token_cost(800, Decimal("0.15")) == Decimal("0.00012")
    assert token_cost(200, Decimal("0.075")) == Decimal("0.000015")
    assert format_usd(None) == "-"
    assert format_usd(Decimal("0.1")) == "0.100000"


def test_percentiles_match_linear_rank_and_hide_small_samples() -> None:
    values = [100 + index * 10 for index in range(20)]
    assert percentile_linear(values, 50) == pytest.approx(195)
    assert percentile_linear(values, 95) == pytest.approx(280.5)
    assert percentile_linear(values, 99) == pytest.approx(288.1)
    assert percentiles_or_none(values[:19]) is None
    found = percentiles_or_none(values)
    assert found is not None
    assert found[50] == pytest.approx(195)
    assert rate(1, 4) == pytest.approx(0.25)
    assert rate(0, 0) is None
    with pytest.raises(ValueError):
        percentile_linear([], 50)


def test_hashing_drops_nothing_but_returns_stable_digests() -> None:
    text = "x" * 1024
    digest, prefixes = hash_content_parts([text, {"role": "user", "content": "y"}])
    assert digest == content_hash(text + "\ny")
    assert prefixes[0][0] == 256
    assert prefix_hashes("short") == ()
    assert hash_content_parts([None, 1, ""]) == (None, ())
    nested, _ = hash_content_parts([[{"content": "ab"}]])
    assert nested == content_hash("ab")


def test_store_keeps_columns_and_hides_missing_duration() -> None:
    store = TraceStore()
    store.append_call(
        LLMCall.model_construct(
            source="jsonl",
            record_id="1",
            trace_id=None,
            parent_id=None,
            session_id=None,
            user_id=None,
            endpoint="e",
            provider="openai",
            model="gpt-4o-mini",
            operation=None,
            start_time=datetime(2026, 10, 1, tzinfo=timezone.utc),
            duration_ms=None,
            input_tokens=3,
            output_tokens=4,
            cached_input_tokens=None,
            reasoning_tokens=None,
            request_count=1,
            status=Status.OK,
            http_status=None,
            error_type=None,
            attempt=None,
            is_batch=None,
            interactive=None,
            prefix_hashes=None,
            content_hash=None,
            tool_name=None,
            args_hash=None,
            reported_cost_usd=None,
            attrs={},
        )
    )
    assert len(store) == 1
    assert store.duration_at(0) is None
    assert store.http_status_at(0) is None
    assert int(store.input_tokens[0]) == 3
    assert store.capabilities is None
    store.note_capabilities(frozenset({Capability.LATENCY, Capability.ERRORS}))
    store.note_capabilities(frozenset({Capability.LATENCY}))
    assert store.capabilities == frozenset({Capability.LATENCY})
    store.warn("same")
    store.warn("same")
    assert store.warnings == ["same"]
    assert date(2026, 10, 15).isoformat() == "2026-10-15"
