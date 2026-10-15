# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracetally.config import ReaderConfig
from tracetally.core.schema import SpanKind
from tracetally.readers.base import load_paths, select_reader
from tracetally.readers.otel import LARGE_DOCUMENT_BYTES, OtelReader

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "otel"


def test_legacy_and_openinference_attributes() -> None:
    legacy = load_paths([FIXTURES / "legacy.json"], ReaderConfig())
    assert len(legacy) == 1
    assert legacy.provider[0] == "openai"
    assert legacy.model[0] == "gpt-4o-mini"
    assert int(legacy.input_tokens[0]) == 1000
    assert int(legacy.output_tokens[0]) == 50
    assert legacy.duration_at(0) == pytest.approx(150)
    assert legacy.content_hash[0]
    assert "CANARY_OTEL_PROMPT" not in " ".join(legacy.warnings)
    assert "CANARY_OTEL_PROMPT" not in (legacy.content_hash[0] or "")

    inferred = load_paths([FIXTURES / "openinference.json"], ReaderConfig())
    assert len(inferred) == 1
    assert inferred.endpoint[0] == "support.reply"
    assert int(inferred.cached_input_tokens[0]) == 100
    assert int(inferred.input_tokens[0]) == 800
    assert len(inferred.spans) == 1
    assert inferred.spans[0].kind is SpanKind.RETRIEVER


def test_jsonl_export_skips_bad_lines_and_streams() -> None:
    store = load_paths([FIXTURES / "lines.jsonl"], ReaderConfig())
    assert len(store) == 1
    assert store.inputs[0].skipped == 2
    warning = " ".join(store.warnings)
    assert "malformed JSON" in warning
    assert "start_time" in warning
    assert "this is not json" not in warning


def test_response_model_wins_and_tool_span_is_not_a_call(tmp_path: Path) -> None:
    document = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "t",
                                "spanId": "s",
                                "name": "chat",
                                "startTimeUnixNano": "1760000000000000000",
                                "endTimeUnixNano": "1760000000050000000",
                                "attributes": [
                                    {
                                        "key": "gen_ai.provider.name",
                                        "value": {"stringValue": "openai"},
                                    },
                                    {
                                        "key": "gen_ai.request.model",
                                        "value": {"stringValue": "gpt-4o"},
                                    },
                                    {
                                        "key": "gen_ai.response.model",
                                        "value": {"stringValue": "gpt-4o-mini"},
                                    },
                                    {
                                        "key": "gen_ai.usage.input_tokens",
                                        "value": {"intValue": "10"},
                                    },
                                    {
                                        "key": "gen_ai.usage.output_tokens",
                                        "value": {"intValue": "1"},
                                    },
                                ],
                                "status": {"code": "STATUS_CODE_ERROR"},
                            }
                        ]
                    }
                ]
            }
        ]
    }
    path = tmp_path / "one.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    store = load_paths([path], ReaderConfig())
    assert store.model[0] == "gpt-4o-mini"
    assert store.status[0] == "error"
    assert select_reader(path, None) is OtelReader


def test_large_single_document_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tracetally.readers.otel.LARGE_DOCUMENT_BYTES", 20)
    path = tmp_path / "big.json"
    path.write_text(FIXTURES.joinpath("legacy.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert path.stat().st_size > 20
    store = load_paths([path], ReaderConfig())
    assert len(store) == 1
    assert any("500 MB" in warning or "loaded whole" in warning for warning in store.warnings)
    assert LARGE_DOCUMENT_BYTES == 500 * 1024 * 1024


def test_sniff_does_not_read_past_the_head(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_bytes(b'{"model":"gpt-4o-mini","input_tokens":1}\n' + b"x" * 80_000)
    with path.open("rb") as handle:
        head = handle.read(64 * 1024)
    assert OtelReader.sniff(path, head) == 0.0
    from tracetally.readers.jsonl import JsonlReader

    assert JsonlReader.sniff(path, head) > 0.5
