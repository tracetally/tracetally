# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from tests.support.factories import (
    write_langfuse_csv,
    write_langfuse_observations,
    write_langfuse_traces,
)
from tracetally.config import ReaderConfig
from tracetally.core.errors import ReaderError
from tracetally.core.schema import Capability, SpanKind, Status
from tracetally.readers.base import load_paths, select_reader
from tracetally.readers.langfuse import LangfuseReader

CANARY = "CANARY_PROMPT_DO_NOT_LEAK"


def test_sniff_prefers_langfuse_over_jsonl_and_ignores_otel(tmp_path: Path) -> None:
    observations = write_langfuse_observations(tmp_path)
    assert select_reader(observations, None) is LangfuseReader
    assert LangfuseReader.sniff(observations, observations.read_bytes()[:100]) > 0.9
    otel = b'{"resourceSpans": []}'
    assert LangfuseReader.sniff(Path("x.json"), otel) == 0.0
    csv_path = write_langfuse_csv(tmp_path)
    assert select_reader(csv_path, None) is LangfuseReader
    canonical = (
        b'{"trace_id":"t","model":"gpt-4o-mini","start_time":"2026-10-01T00:00:00Z",'
        b'"input_tokens":1,"output_tokens":1}\n'
    )
    assert LangfuseReader.sniff(Path("calls.jsonl"), canonical) == 0.0
    blob = b'{"trace_id":"t","type":"GENERATION","usage_details":{"input":1,"output":1}}\n'
    assert LangfuseReader.sniff(Path("obs.json"), blob) == 0.96


def test_json_observations_map_generations_spans_and_costs(tmp_path: Path) -> None:
    store = load_paths([write_langfuse_observations(tmp_path)], ReaderConfig())
    assert store.readers == {"langfuse"}
    assert Capability.TRACE_TREE in (store.capabilities or set())
    assert len(store) == 1
    assert store.inputs[0].skipped == 1
    assert store.inputs[0].spans == 2
    assert store.model[0] == "gpt-4o-mini"
    assert store.provider[0] == "openai"
    assert store.endpoint[0] == "support.reply"
    assert store.input_tokens[0] == 1000
    assert store.output_tokens[0] == 100
    assert store.cached_input_tokens[0] == 200
    assert store.reasoning_tokens[0] == 40
    assert store.duration_at(0) == pytest.approx(1500)
    assert store.reported_cost_usd[0] == Decimal("0.001")
    assert store.trace_id[0] == "trace-1"
    assert store.status[0] == "ok"
    kinds = {span.kind for span in store.spans}
    assert kinds == {SpanKind.TOOL, SpanKind.RETRIEVER}
    tool = next(span for span in store.spans if span.name == "search")
    assert tool.parent_id == "gen-1"
    assert tool.args_hash
    blob = " ".join(store.warnings)
    assert CANARY not in blob
    assert "CANARY_OUTPUT_DO_NOT_LEAK" not in blob
    assert "CANARY_TOOL" not in blob
    assert "model" in blob
    assert "non-ISO" in blob or "latency is interpreted as seconds" in blob


def test_endpoint_attr_and_strict_missing_model(tmp_path: Path) -> None:
    chosen = load_paths(
        [write_langfuse_observations(tmp_path)],
        ReaderConfig(endpoint_attr="feature"),
    )
    assert chosen.endpoint[0] == "reply"
    with pytest.raises(ReaderError, match="model"):
        load_paths([write_langfuse_observations(tmp_path)], ReaderConfig(strict=True))


def test_trace_export_expands_observations_and_skips_a_model_less_trace(tmp_path: Path) -> None:
    store = load_paths([write_langfuse_traces(tmp_path)], ReaderConfig())
    assert len(store) == 2
    assert store.inputs[0].skipped == 1
    by_model = dict(zip(store.model, range(len(store)), strict=True))
    draft = by_model["claude-sonnet-4-5"]
    assert store.provider[draft] == "anthropic"
    assert store.endpoint[draft] == "draft"
    assert store.trace_id[draft] == "trace-9"
    assert store.record_id[draft] == "gen-9"
    assert store.input_tokens[draft] == 100
    assert store.output_tokens[draft] == 50
    assert store.duration_at(draft) == pytest.approx(2000)
    assert store.status[draft] == Status.ERROR.value
    assert store.http_status_at(draft) == 503
    assert store.is_batch[draft] == 1
    embed = by_model["text-embedding-3-small"]
    assert store.provider[embed] == "openai"
    assert "trace has no model" in " ".join(store.warnings)


def test_csv_aliases_dates_and_extra_columns(tmp_path: Path) -> None:
    store = load_paths([write_langfuse_csv(tmp_path)], ReaderConfig())
    assert len(store) == 1
    assert store.spans and len(store.spans) == 2
    assert store.model[0] == "gpt-4o-mini"
    assert store.input_tokens[0] == 500
    assert store.output_tokens[0] == 25
    assert store.reported_cost_usd[0] == Decimal("0.004")
    assert store.start_time[0] == datetime(2026, 10, 2, tzinfo=timezone.utc)
    assert store.endpoint[0] == "support.reply"
    warnings = " ".join(store.warnings)
    assert "column aliases" in warnings
    assert "ignored extra columns" in warnings
    assert "Comment" in warnings
    assert store.inputs[0].skipped == 1


def test_lenient_jsonl_and_character_usage(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "ok",
                        "traceId": "t",
                        "type": "GENERATION",
                        "name": "chat",
                        "startTime": "2026-10-04T00:00:00Z",
                        "model": "gemini-2.5-flash",
                        "usage": {"unit": "TOKENS", "input": 8, "total": 10},
                    }
                ),
                json.dumps(
                    {
                        "id": "chars",
                        "traceId": "t",
                        "type": "GENERATION",
                        "startTime": "2026-10-04T00:00:01Z",
                        "model": "gemini-2.5-flash",
                        "usage": {"unit": "CHARACTERS", "input": 8, "output": 2},
                    }
                ),
                "{not json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    store = load_paths([path], ReaderConfig())
    assert len(store) == 1
    assert store.output_tokens[0] == 2
    assert store.provider[0] == "gemini"
    warnings = " ".join(store.warnings)
    assert "derived from the total" in warnings
    assert "CHARACTERS" in warnings
    assert "malformed JSON" in warnings
    assert select_reader(path, None) is LangfuseReader


def test_ambiguous_slash_date_is_not_guessed(tmp_path: Path) -> None:
    path = tmp_path / "ambiguous.csv"
    path.write_text(
        "id,traceId,type,name,startTime,model,inputTokens,outputTokens\n"
        "1,t,GENERATION,chat,10/11/2026 00:00:00,gpt-4o-mini,1,1\n",
        encoding="utf-8",
    )
    store = load_paths([path], ReaderConfig())
    assert len(store) == 0
    assert "ambiguous" in " ".join(store.warnings)
    with pytest.raises(ReaderError, match="ambiguous"):
        load_paths([path], ReaderConfig(strict=True))
