# SPDX-License-Identifier: Apache-2.0
"""Reader protocol, registry, and format auto-detection.

Plugins are loaded by importing entry points. Nothing is downloaded.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import ClassVar, Protocol

from tracetally.config import ReaderConfig
from tracetally.core.errors import ReaderError
from tracetally.core.schema import Capability, LLMCall, Span
from tracetally.core.store import InputStat, TraceStore

SNIFF_HEAD_BYTES = 64 * 1024
_AMBIGUITY_EPSILON = 1e-9
_DETECT_THRESHOLD = 0.5


class Reader(Protocol):
    """File-in reader. ``read`` streams; it must not hold the raw file."""

    name: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]]

    @classmethod
    def sniff(cls, path: Path, head: bytes) -> float:
        """Confidence in ``[0, 1]`` from at most 64 KB of ``head``."""
        ...

    def read(self, path: Path, cfg: ReaderConfig) -> Iterator[LLMCall | Span]: ...


class BaseReader:
    """Shared warning buffer. Concrete readers subclass this."""

    name: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]]
    max_detailed_warnings: ClassVar[int] = 20

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.skipped: int = 0
        self._suppressed = 0

    def warn(self, message: str) -> None:
        if len(self.warnings) < self.max_detailed_warnings:
            self.warnings.append(message)
        else:
            self._suppressed += 1

    def finish_warnings(self) -> None:
        if self._suppressed:
            self.warnings.append(f"{self._suppressed} further reader warnings suppressed")
            self._suppressed = 0

    def fail(self, message: str, *, field: str | None, where: str, cfg: ReaderConfig) -> None:
        self.skipped += 1
        if cfg.strict:
            raise ReaderError(message, field=field, where=where)
        if field:
            self.warn(f"{where}: {message} (field {field!r}); skipped")
        else:
            self.warn(f"{where}: {message}; skipped")

    @classmethod
    def sniff(cls, path: Path, head: bytes) -> float:
        raise NotImplementedError

    def read(self, path: Path, cfg: ReaderConfig) -> Iterator[LLMCall | Span]:
        raise NotImplementedError


def _builtin_readers() -> dict[str, type[BaseReader]]:
    # Imported lazily so this module can be imported by the reader modules.
    from tracetally.readers.jsonl import JsonlReader
    from tracetally.readers.langfuse import LangfuseReader
    from tracetally.readers.openai_csv import OpenAICsvReader
    from tracetally.readers.otel import OtelReader

    return {
        JsonlReader.name: JsonlReader,
        OtelReader.name: OtelReader,
        LangfuseReader.name: LangfuseReader,
        OpenAICsvReader.name: OpenAICsvReader,
    }


def registry() -> dict[str, type[BaseReader]]:
    """Entry-point readers, with built-ins filled in if a name is missing.

    A checkout that has not been installed still sees the built-in readers.
    Loading a plugin is a local import. A plugin that fails to import is
    reported and skipped so one broken extra cannot hide the built-ins.
    """
    found: dict[str, type[BaseReader]] = {}
    problems: list[str] = []
    registry_problems.clear()
    eps: tuple[EntryPoint, ...] = tuple(entry_points(group="tracetally.readers"))
    for ep in eps:
        try:
            loaded = ep.load()
        except Exception as exc:  # a third-party distribution can raise anything
            problems.append(f"{ep.name}: {exc.__class__.__name__}: {exc}")
            continue
        if not isinstance(loaded, type) or not hasattr(loaded, "sniff"):
            problems.append(f"{ep.name}: entry point did not resolve to a reader class")
            continue
        found[ep.name] = loaded
    for name, cls in _builtin_readers().items():
        found.setdefault(name, cls)
    if problems:
        # Stash on the function so ``tracetally readers`` can surface them.
        registry_problems.clear()
        registry_problems.extend(problems)
    return found


registry_problems: list[str] = []


def read_head(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(SNIFF_HEAD_BYTES)


def select_reader(path: Path, format_name: str | None) -> type[BaseReader]:
    readers = registry()
    if format_name:
        key = format_name.strip().lower()
        if key not in readers:
            known = ", ".join(sorted(readers)) or "(none)"
            raise ReaderError(
                f"unknown format {format_name!r}; known formats: {known}",
                where=str(path),
            )
        return readers[key]

    head = read_head(path)
    scores = {name: float(cls.sniff(path, head)) for name, cls in readers.items()}
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    if not ranked or ranked[0][1] < _DETECT_THRESHOLD:
        detail = ", ".join(f"{name}={score:.2f}" for name, score in ranked) or "no readers"
        raise ReaderError(
            f"could not detect the trace format ({detail}); pass --format",
            where=str(path),
        )
    best_name, best_score = ranked[0]
    if len(ranked) > 1:
        second_name, second_score = ranked[1]
        if (
            second_score >= _DETECT_THRESHOLD
            and abs(best_score - second_score) <= _AMBIGUITY_EPSILON
        ):
            raise ReaderError(
                f"ambiguous format between {best_name} ({best_score:.2f}) "
                f"and {second_name} ({second_score:.2f}); pass --format",
                where=str(path),
            )
    return readers[best_name]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_paths(paths: list[Path], cfg: ReaderConfig) -> TraceStore:
    """Stream every path into one store. Capabilities are the intersection."""
    store = TraceStore()
    if not paths:
        raise ReaderError("no input files")
    for path in paths:
        if not path.exists():
            raise ReaderError("file not found", where=str(path))
        if not path.is_file():
            raise ReaderError("not a file", where=str(path))
        reader_cls = select_reader(path, cfg.format_name)
        reader = reader_cls()
        stat = InputStat(path=str(path), sha256=file_sha256(path))
        store.note_capabilities(frozenset(reader_cls.capabilities))
        store.note_reader(reader_cls.name)
        try:
            for item in reader.read(path, cfg):
                if isinstance(item, LLMCall):
                    store.append_call(item)
                    stat.records += 1
                elif isinstance(item, Span):
                    store.append_span(item)
                    stat.spans += 1
                else:
                    reader.fail(
                        f"reader yielded {type(item).__name__}",
                        field=None,
                        where=str(path),
                        cfg=cfg,
                    )
        finally:
            reader.finish_warnings()
        stat.skipped = reader.skipped
        for warning in reader.warnings:
            store.warn(f"{reader_cls.name}: {warning}")
        store.inputs.append(stat)
    return store
