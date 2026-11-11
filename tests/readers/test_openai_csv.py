# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from tests.support.factories import write_openai_costs_csv, write_openai_usage_csv
from tracetally.analysis.summary import summarize
from tracetally.config import ReaderConfig
from tracetally.core.errors import ReaderError
from tracetally.core.schema import Capability
from tracetally.pricing.table import load_bundled
from tracetally.readers.base import load_paths, select_reader
from tracetally.readers.openai_csv import OpenAICsvReader


def test_sniff_detects_usage_csv_and_not_langfuse(tmp_path: Path) -> None:
    usage = write_openai_usage_csv(tmp_path)
    assert select_reader(usage, None) is OpenAICsvReader
    assert OpenAICsvReader.sniff(usage, b"traceId,model,input_tokens\n") == 0.0
    assert OpenAICsvReader.capabilities == frozenset()


def test_usage_rows_are_aggregated_and_a_bad_date_is_skipped(tmp_path: Path) -> None:
    store = load_paths([write_openai_usage_csv(tmp_path)], ReaderConfig())
    assert store.readers == {"openai_csv"}
    assert store.capabilities == frozenset()
    assert Capability.LATENCY not in (store.capabilities or set())
    assert len(store) == 2
    assert store.inputs[0].skipped == 1
    assert store.provider[0] == "openai"
    assert store.model[0] == "gpt-4o-mini"
    assert store.endpoint[0] == "proj_a"
    assert int(store.request_count[0]) == 10
    assert int(store.input_tokens[0]) == 1_000_000
    assert int(store.cached_input_tokens[0]) == 200_000
    assert store.duration_at(0) is None
    assert store.trace_id[0] is None
    assert store.model[1] == "gpt-4o"
    assert int(store.request_count[1]) == 2
    assert store.is_batch[1] == 1
    assert store.start_time[1] == datetime.fromtimestamp(1_727_740_800, tz=timezone.utc)
    warnings = " ".join(store.warnings)
    assert "num_model_requests -> request_count" in warnings
    assert "ignored extra columns" in warnings
    assert "notes" in warnings
    assert "ISO-8601" in warnings or "timestamp" in warnings


def test_cost_csv_uses_aliases_and_keeps_reported_cost(tmp_path: Path) -> None:
    store = load_paths(
        [write_openai_costs_csv(tmp_path)], ReaderConfig(endpoint_attr="project_name")
    )
    assert len(store) == 1
    assert store.inputs[0].skipped == 1
    assert store.model[0] == "gpt-4o-mini-2024-07-18"
    assert store.endpoint[0] == "Acme"
    assert store.reported_cost_usd[0] == Decimal("1.25")
    assert int(store.input_tokens[0]) == 0
    assert int(store.request_count[0]) == 1
    warnings = " ".join(store.warnings)
    assert "column aliases" in warnings
    assert "semicolon" not in warnings
    assert "no token counts" in warnings
    assert "request_count was missing" in warnings
    assert "EUR" in warnings or "eur" in warnings
    result = summarize(store, load_bundled())
    assert result.mode == "cost-only"
    assert result.latency_measured is False
    assert result.errors_measured is False
    assert result.priced_calls == 1
    assert result.cost_usd == Decimal("1.25")
    assert result.reported_cost_usd == Decimal("1.25")
    names = {item.check for item in result.skipped_checks}
    assert "agent_loops" in names
    assert "latency_tail" in names
    assert "error_rates" in names
    assert "cache_opportunity" in names
    text = result.to_text()
    assert "Cost-only mode" in text
    assert "n/a" in text
    assert "not measured" in text
    payload = result.to_dict()
    assert payload["mode"] == "cost-only"
    assert payload["error_rates"]["measured"] is False
    assert payload["error_rates"]["rate_429"] is None
    assert any(item["check"] == "agent_loops" for item in payload["skipped_checks"])


def test_strict_unknown_header_and_cents(tmp_path: Path) -> None:
    cents = tmp_path / "cents.csv"
    cents.write_text(
        "start_time,model,amount_cents\n2026-10-01T00:00:00Z,gpt-4o-mini,125\n",
        encoding="utf-8",
    )
    store = load_paths([cents], ReaderConfig())
    assert len(store) == 0
    assert "amount_cents" in " ".join(store.warnings)
    with pytest.raises(ReaderError, match="amount_cents"):
        load_paths([cents], ReaderConfig(strict=True))

    wrong = tmp_path / "nope.csv"
    wrong.write_text("foo,bar\n1,2\n", encoding="utf-8")
    assert OpenAICsvReader.sniff(wrong, wrong.read_bytes()) == 0.0
    with pytest.raises(ReaderError, match="header"):
        load_paths([wrong], ReaderConfig(format_name="openai_csv", strict=True))


def test_dashboard_cached_input_tokens_header(tmp_path: Path) -> None:
    """Usage-dashboard export spells the cache column 'Cached Input Tokens'."""
    path = tmp_path / "dashboard.csv"
    path.write_text(
        "Date,Organization ID,Project ID,Model,Requests,Input Tokens,Output Tokens,"
        "Cached Input Tokens,Cost ($)\n"
        "2026-11-01,org-prod123,proj-default,gpt-4o,1420,1245000,680000,310000,10.15\n",
        encoding="utf-8",
    )
    assert select_reader(path, None) is OpenAICsvReader
    store = load_paths([path], ReaderConfig())
    assert len(store) == 1
    assert store.inputs[0].skipped == 0
    assert int(store.input_tokens[0]) == 1_245_000
    assert int(store.cached_input_tokens[0]) == 310_000
    assert int(store.output_tokens[0]) == 680_000
    assert int(store.request_count[0]) == 1420
    assert store.endpoint[0] == "proj-default"
    assert store.reported_cost_usd[0] == Decimal("10.15")
    warnings = " ".join(store.warnings)
    assert "ignored extra columns" not in warnings
    assert "Cached Input Tokens -> cached_input_tokens" in warnings
    result = summarize(store, load_bundled())
    # The CSV's billed cost beats the inferred list price.
    assert result.cost_usd == Decimal("10.15")


def test_audio_tokens_are_not_added(tmp_path: Path) -> None:
    path = tmp_path / "audio.csv"
    path.write_text(
        "date,snapshot_id,n_requests,n_context_tokens_total,n_generated_tokens_total,"
        "input_audio_tokens\n"
        '2026-10-05,gpt-4o-mini,3,"1,000",10,50\n',
        encoding="utf-8",
    )
    store = load_paths([path], ReaderConfig())
    assert len(store) == 1
    assert int(store.input_tokens[0]) == 1000
    assert int(store.output_tokens[0]) == 10
    assert int(store.request_count[0]) == 3
    assert store.start_time[0] == datetime(2026, 10, 5, tzinfo=timezone.utc)
    assert "audio tokens" in " ".join(store.warnings)
    assert "column aliases" in " ".join(store.warnings)
