# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

from tracetally.core.errors import ReaderError
from tracetally.readers.base import BaseReader, registry, select_reader
from tracetally.readers.jsonl import JsonlReader
from tracetally.readers.otel import OtelReader


def test_builtins_are_registered_through_the_entry_point_group() -> None:
    from importlib.metadata import entry_points

    names = {ep.name for ep in entry_points(group="tracetally.readers")}
    assert {"jsonl", "otel", "langfuse", "openai_csv"} <= names
    found = registry()
    assert found["jsonl"] is JsonlReader
    assert found["otel"] is OtelReader
    assert found["langfuse"].name == "langfuse"
    assert found["openai_csv"].capabilities == frozenset()
    assert "per_call" in {cap.value for cap in found["jsonl"].capabilities}


def test_ambiguous_sniff_names_both_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Alpha(BaseReader):
        name = "alpha"
        capabilities = frozenset()

        @classmethod
        def sniff(cls, path: Path, head: bytes) -> float:
            return 0.8

        def read(self, path: Path, cfg: object):
            yield from ()

    class Beta(BaseReader):
        name = "beta"
        capabilities = frozenset()

        @classmethod
        def sniff(cls, path: Path, head: bytes) -> float:
            return 0.8

        def read(self, path: Path, cfg: object):
            yield from ()

    monkeypatch.setattr(
        "tracetally.readers.base.registry",
        lambda: {"alpha": Alpha, "beta": Beta},
    )
    path = tmp_path / "x.json"
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ReaderError, match="ambiguous"):
        select_reader(path, None)


def test_unknown_format_names_the_known_readers(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ReaderError, match="unknown format"):
        select_reader(path, "csv")


def test_undetectable_file_asks_for_format(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("hello\n", encoding="utf-8")
    with pytest.raises(ReaderError, match="could not detect"):
        select_reader(path, None)
