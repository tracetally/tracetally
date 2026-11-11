# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.support.factories import call_record, write_jsonl_records
from tracetally.cli import EXIT_OK, app
from tracetally.config import ReaderConfig
from tracetally.core.schema import LLMCall, Span, SpanKind, Status
from tracetally.readers.dedup import _identity_ids, _stable, dropped_message, load_deduped

runner = CliRunner()


def _call(
    record_id: str,
    trace_id: str,
    when: str,
    model: str = "gpt-4o-mini",
) -> dict[str, object]:
    return call_record(
        record_id=record_id,
        trace_id=trace_id,
        start_time=when,
        model=model,
    )


def test_identical_files_are_counted_once(tmp_path: Path) -> None:
    first = write_jsonl_records(tmp_path, [_call("req-1", "t-1", "2026-10-01T00:00:00Z")])
    second = tmp_path / "copy.jsonl"
    shutil.copyfile(first, second)
    loaded = load_deduped([first, second], ReaderConfig())
    assert len(loaded.files) == 1
    assert loaded.store.records_dropped == 0
    assert loaded.skipped[0].reason.startswith("duplicate of ")
    assert any("duplicate file" in warning for warning in loaded.store.warnings)
    assert len(loaded.store) == 1


def test_shared_ids_are_dropped_and_same_file_ids_stay(tmp_path: Path) -> None:
    left = write_jsonl_records(
        tmp_path,
        [
            _call("req-1", "trace-a", "2026-10-01T00:00:00Z"),
            _call("req-1", "trace-a", "2026-10-01T00:05:00Z"),
        ],
        name="a.jsonl",
    )
    right = write_jsonl_records(
        tmp_path,
        [
            _call("req-1", "trace-a", "2026-10-01T00:10:00Z"),
            _call("req-2", "trace-b", "2026-10-01T00:20:00Z"),
        ],
        name="b.jsonl",
    )
    loaded = load_deduped([left, right], ReaderConfig())
    assert len(loaded.store) == 3
    assert loaded.store.records_dropped == 1
    assert dropped_message(1) in loaded.store.warnings
    assert loaded.overlap == []


def test_trace_and_span_ids_drop_across_files(tmp_path: Path) -> None:
    first = write_jsonl_records(
        tmp_path,
        [_call("keep-1", "shared-trace", "2026-10-01T00:00:00Z")],
        name="first.jsonl",
    )
    second = write_jsonl_records(
        tmp_path,
        [_call("keep-2", "shared-trace", "2026-10-01T01:00:00Z")],
        name="second.jsonl",
    )
    traced = load_deduped([first, second], ReaderConfig())
    assert traced.store.records_dropped == 1
    assert len(traced.store) == 1

    span_a = write_jsonl_records(
        tmp_path,
        [
            {
                "kind": "tool",
                "name": "search",
                "span_id": "span-9",
                "trace_id": "trace-span",
                "start_time": "2026-10-02T00:00:00Z",
            }
        ],
        name="span-a.jsonl",
    )
    span_b = write_jsonl_records(
        tmp_path,
        [
            {
                "kind": "tool",
                "name": "search",
                "span_id": "span-9",
                "trace_id": "trace-other",
                "start_time": "2026-10-03T00:00:00Z",
            }
        ],
        name="span-b.jsonl",
    )
    spanned = load_deduped([span_a, span_b], ReaderConfig())
    assert len(spanned.store.spans) == 1
    assert spanned.store.records_dropped == 1


def test_similar_files_without_ids_warn_and_are_kept(tmp_path: Path) -> None:
    left = write_jsonl_records(
        tmp_path,
        [
            _call("a-1", "trace-a", "2026-10-01T00:00:00Z"),
            _call("a-2", "trace-a2", "2026-10-01T02:00:00Z"),
        ],
        name="left.jsonl",
    )
    right = write_jsonl_records(
        tmp_path,
        [_call("b-1", "trace-b", "2026-10-01T01:00:00Z")],
        name="right.jsonl",
    )
    far = write_jsonl_records(
        tmp_path,
        [_call("c-1", "trace-c", "2026-12-01T00:00:00Z", model="gpt-4o")],
        name="far.jsonl",
    )
    loaded = load_deduped([left, right, far], ReaderConfig())
    assert len(loaded.store) == 4
    assert loaded.store.records_dropped == 0
    assert len(loaded.overlap) == 1
    assert "kept both" in loaded.overlap[0]
    assert "gpt-4o-mini" in loaded.overlap[0]
    assert "far.jsonl" not in loaded.overlap[0]


def test_location_ids_are_not_shared_across_files() -> None:
    source = r"C:\data\calls.jsonl"
    assert _stable(source + ":1", source) is False
    assert _stable(source + " spans[0]", source) is False
    assert _stable("req-7", source) is True
    call = LLMCall.model_construct(
        source="jsonl",
        record_id=source + ":1",
        trace_id=None,
        endpoint="(unknown)",
        provider="openai",
        model="gpt-4o-mini",
        start_time=datetime(2026, 10, 1, tzinfo=timezone.utc),
        input_tokens=1,
        output_tokens=1,
        status=Status.OK,
    )
    assert _identity_ids(call, source) == set()
    span = Span.model_construct(
        source="jsonl",
        trace_id=None,
        span_id=source + "[0]",
        name="tool",
        kind=SpanKind.TOOL,
        start_time=call.start_time,
        status=Status.OK,
    )
    assert _identity_ids(span, source) == set()


def test_run_shows_dropped_records_in_the_console_and_findings(tmp_path: Path) -> None:
    first = write_jsonl_records(
        tmp_path,
        [_call("req-9", "t-9", "2026-10-01T00:00:00Z")],
        name="one.jsonl",
    )
    second = write_jsonl_records(
        tmp_path,
        [_call("req-9", "t-9", "2026-10-01T00:01:00Z", model="gpt-4o")],
        name="two.jsonl",
    )
    out = tmp_path / "out"
    result = CliRunner().invoke(
        app,
        ["run", str(first), str(second), "--offline", "--out", str(out), "--no-open"],
    )
    assert result.exit_code == EXIT_OK, result.output
    assert "dropped 1 duplicate record" in result.stdout
    payload = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert payload["records_dropped"] == 1
    assert payload["totals"]["calls"] == 1
    assert any(warning == "dropped 1 duplicate record" for warning in payload["warnings"])


def test_missing_file_is_a_warning(tmp_path: Path) -> None:
    loaded = load_deduped([tmp_path / "missing.jsonl"], ReaderConfig())
    assert loaded.files == []
    assert any("file skipped" in warning for warning in loaded.store.warnings)
    assert dropped_message(2) == "dropped 2 duplicate records"


def test_explicit_symlink_file_and_ancestor_are_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    from tracetally.readers.base import file_sha256

    opened: list[str] = []

    def tracking(path: Path) -> str:
        opened.append(str(path))
        return file_sha256(path)

    monkeypatch.setattr("tracetally.readers.dedup.file_sha256", tracking)
    loaded = load_deduped(
        [alias, linkparent / "nested" / "secret.jsonl", ordinary],
        ReaderConfig(),
    )
    assert loaded.store.record_id == ["ordinary"]
    assert opened == [str(ordinary)]
    assert {note.path: note.reason for note in loaded.skipped} == {
        "alias.jsonl": "symlink",
        "linkparent": "symlink",
    }
