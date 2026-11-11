# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import types
from pathlib import Path

import pytest

from tests.support.factories import write_array_json, write_messy_jsonl, write_pretty_json
from tracetally.config import ReaderConfig
from tracetally.core.errors import ReaderError
from tracetally.core.schema import LLMCall
from tracetally.readers.base import load_paths
from tracetally.readers.jsonl import JsonlReader

CANARY = "CANARY_PROMPT_DO_NOT_LEAK"
BROKEN = "CANARY_BROKEN_LINE"


def test_messy_file_skips_bad_lines_and_drops_prompt_text(tmp_path: Path) -> None:
    store = load_paths([write_messy_jsonl(tmp_path)], ReaderConfig())
    assert len(store) == 1
    assert store.inputs[0].skipped == 5
    assert store.model[0] == "gpt-4o-mini"
    assert store.content_hash[0]
    blob = " ".join(store.warnings)
    assert CANARY not in blob
    assert BROKEN not in blob
    assert "malformed JSON" in blob
    assert "input_tokens" in blob
    assert "model" in blob
    assert "secret-key" not in blob
    call_text = " ".join(str(item) for item in store.content_hash)
    assert CANARY not in call_text


def test_strict_mode_raises_on_the_first_bad_line(tmp_path: Path) -> None:
    with pytest.raises(ReaderError, match="malformed JSON"):
        load_paths([write_messy_jsonl(tmp_path)], ReaderConfig(strict=True))


def test_pretty_document_and_array(tmp_path: Path) -> None:
    pretty = load_paths([write_pretty_json(tmp_path)], ReaderConfig())
    assert len(pretty) == 1
    assert pretty.endpoint[0] == "support.reply"
    assert pretty.duration_at(0) == pytest.approx(150)
    array = load_paths([write_array_json(tmp_path)], ReaderConfig())
    assert len(array) == 1
    assert array.inputs[0].skipped == 1
    assert "input_tokens" in " ".join(array.warnings)


def test_endpoint_attr_and_streaming(tmp_path: Path) -> None:
    path = tmp_path / "many calls.jsonl"
    line = (
        '{"model":"gpt-4o-mini","provider":"openai","start_time":"2026-10-01T00:00:00Z",'
        '"input_tokens":1,"output_tokens":1,"feature":"reply"}\n'
    )
    path.write_text(line * 3000, encoding="utf-8")
    reader = JsonlReader()
    iterator = reader.read(path, ReaderConfig(endpoint_attr="feature"))
    assert isinstance(iterator, types.GeneratorType)
    first = next(iterator)
    assert isinstance(first, LLMCall)
    assert first.endpoint == "reply"
    assert first.attrs.get("feature") == "reply" or first.endpoint == "reply"
    rest = sum(1 for _ in iterator)
    assert rest == 2999
    assert reader.skipped == 0


def test_prefix_hash_for_a_long_prompt(tmp_path: Path) -> None:
    import json

    prompt = "p" * 1024
    path = tmp_path / "prompt.jsonl"
    path.write_text(
        json.dumps(
            {
                "model": "gpt-4o-mini",
                "provider": "openai",
                "start_time": "2026-10-01T00:00:00Z",
                "input_tokens": 256,
                "output_tokens": 1,
                "prompt": prompt,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = load_paths([path], ReaderConfig())
    assert len(store) == 1
    assert store.content_hash[0]
    assert store.prefix_hashes[0][0][0] == 256
    assert prompt not in (store.content_hash[0] or "")


def test_missing_file_is_an_input_error(tmp_path: Path) -> None:
    with pytest.raises(ReaderError, match="file not found"):
        load_paths([tmp_path / "nope.jsonl"], ReaderConfig())
