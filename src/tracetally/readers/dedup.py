# SPDX-License-Identifier: Apache-2.0
"""Count identical files once, then drop records that repeat an id."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tracetally.config import ReaderConfig
from tracetally.core.errors import ReaderError
from tracetally.core.schema import LLMCall, Span
from tracetally.core.store import TraceStore
from tracetally.readers.base import _load_one, file_sha256
from tracetally.readers.discover import SkipNote, _blocking_symlink, relative_display


@dataclass(frozen=True)
class LoadedFile:
    path: Path
    label: str
    format_name: str
    records: int
    size: int
    sha256: str


@dataclass
class DedupResult:
    store: TraceStore
    files: list[LoadedFile]
    skipped: list[SkipNote]
    overlap: list[str]


@dataclass
class _Profile:
    label: str
    models: set[str] = field(default_factory=set)
    start: datetime | None = None
    end: datetime | None = None
    ids: set[str] = field(default_factory=set)


class _Gate:
    """Drop a record only when an earlier file already had one of its ids."""

    def __init__(self, prior: set[str], profile: _Profile, source_path: str) -> None:
        self.prior = prior
        self.profile = profile
        self.source_path = source_path
        self.file_ids: set[str] = set()

    def accept(self, item: LLMCall | Span) -> bool:
        ids = _observe(self.profile, item, self.source_path)
        self.file_ids.update(ids)
        if not ids:
            return True
        return ids.isdisjoint(self.prior)


def dropped_message(count: int) -> str:
    noun = "record" if count == 1 else "records"
    return f"dropped {count} duplicate {noun}"


def load_deduped(paths: Sequence[Path], cfg: ReaderConfig) -> DedupResult:
    """Load paths for ``tracetally run``.

    Byte-identical files are kept once. A later record is dropped when its
    request id, span id, or trace id was already seen in an earlier file.
    The same ids inside one file are kept. Similar files that share no ids
    are kept and named in ``overlap``.

    A path that is a symlink, or that has a symlink ancestor, is skipped
    before it is opened. ``summary``, ``analyze``, and ``report`` do not use
    this loader.
    """
    store = TraceStore()
    files: list[LoadedFile] = []
    skipped: list[SkipNote] = []
    profiles: list[_Profile] = []
    seen_path: set[str] = set()
    seen_hash: dict[str, str] = {}
    seen_ids: set[str] = set()
    dropped = 0

    for path in paths:
        blocked = _blocking_symlink(path)
        if blocked is not None:
            skipped.append(SkipNote(blocked.name or str(path), "symlink"))
            continue
        label = _label(path)
        key = os.path.normcase(os.path.abspath(path))
        if key in seen_path:
            _note_duplicate(store, skipped, label, label)
            continue
        try:
            if not path.exists():
                raise ReaderError("file not found", where=str(path))
            if not path.is_file():
                raise ReaderError("not a file", where=str(path))
            digest = file_sha256(path)
            size = path.stat().st_size
        except ReaderError as exc:
            store.warn(f"file skipped: {exc}")
            continue
        except OSError as exc:
            store.warn(f"file skipped: {path}: {exc}")
            continue
        seen_path.add(key)
        if digest in seen_hash:
            _note_duplicate(store, skipped, label, seen_hash[digest])
            continue
        seen_hash[digest] = label
        profile = _Profile(label=label)
        gate = _Gate(seen_ids, profile, str(path))
        try:
            format_name, file_dropped = _load_one(store, path, cfg, accept=gate.accept)
        except ReaderError as exc:
            store.warn(f"file skipped: {exc}")
            continue
        except OSError as exc:
            store.warn(f"file skipped: {path}: {exc}")
            continue
        dropped += file_dropped
        seen_ids.update(gate.file_ids)
        stat = store.inputs[-1]
        files.append(
            LoadedFile(
                path=path,
                label=label,
                format_name=format_name,
                records=stat.records + stat.spans + file_dropped,
                size=size,
                sha256=digest,
            )
        )
        profiles.append(profile)

    overlap = _overlap_warnings(profiles)
    for message in overlap:
        store.warn(message)
    store.records_dropped = dropped
    if dropped:
        store.warn(dropped_message(dropped))
    return DedupResult(store=store, files=files, skipped=skipped, overlap=overlap)


def _note_duplicate(store: TraceStore, skipped: list[SkipNote], label: str, original: str) -> None:
    skipped.append(SkipNote(label, f"duplicate of {original}"))
    store.warn(f"duplicate file: {label} matches {original}")


def _label(path: Path) -> str:
    root = Path(os.path.abspath(Path.cwd()))
    return relative_display(Path(os.path.abspath(path)), root)


def _observe(profile: _Profile, item: LLMCall | Span, source_path: str) -> set[str]:
    ids = _identity_ids(item, source_path)
    profile.ids.update(ids)
    start = item.start_time
    if profile.start is None or start < profile.start:
        profile.start = start
    if profile.end is None or start > profile.end:
        profile.end = start
    if isinstance(item, LLMCall) and item.model:
        profile.models.add(item.model)
    return ids


def _identity_ids(item: LLMCall | Span, source_path: str) -> set[str]:
    """Stable request, span, and trace ids. File locations are not ids."""
    found: set[str] = set()
    if isinstance(item, LLMCall):
        if _stable(item.record_id, source_path):
            found.add("request:" + item.record_id)
            if item.source == "otel":
                found.add("span:" + item.record_id)
        if item.trace_id:
            found.add("trace:" + item.trace_id)
        return found
    if _stable(item.span_id, source_path):
        found.add("span:" + item.span_id)
    if item.trace_id:
        found.add("trace:" + item.trace_id)
    return found


def _stable(value: str, source_path: str) -> bool:
    if not value or value == source_path:
        return False
    return not value.startswith((source_path + ":", source_path + "[", source_path + " "))


def _overlap_warnings(profiles: Sequence[_Profile]) -> list[str]:
    ordered = sorted(profiles, key=lambda item: item.label)
    found: list[str] = []
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if left.ids & right.ids:
                continue
            shared = left.models & right.models
            if not shared or not _overlaps(left, right):
                continue
            names = ", ".join(sorted(shared))
            found.append(
                f"overlap: {left.label} and {right.label} share {names} "
                "in the same time window and no request, span, or trace id; kept both"
            )
    return found


def _overlaps(left: _Profile, right: _Profile) -> bool:
    if left.start is None or left.end is None or right.start is None or right.end is None:
        return False
    return left.start <= right.end and right.start <= left.end
