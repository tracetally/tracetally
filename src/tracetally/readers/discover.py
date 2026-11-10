# SPDX-License-Identifier: Apache-2.0
"""Find usage exports under a directory. No network. Symlinks are not followed."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath

from tracetally.core.errors import ReaderError, UsageError
from tracetally.core.terminal import terminal_safe
from tracetally.readers.base import select_reader

DEFAULT_MAX_DEPTH = 4
DEFAULT_MAX_FILE_SIZE = 64 * 1024 * 1024
OUTPUT_DIR_NAME = "tracetally-report"

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
        "site-packages",
    }
)
_SKIP_FOLDED = frozenset(name.casefold() for name in SKIP_DIR_NAMES)

CANDIDATE_SUFFIXES = frozenset({".jsonl", ".json", ".csv", ".ndjson"})

FORMAT_LABELS = {
    "jsonl": "JSONL",
    "otel": "OTLP/OTel",
    "langfuse": "Langfuse",
    "openai_csv": "OpenAI usage CSV",
}


@dataclass(frozen=True)
class SkipNote:
    path: str
    reason: str


@dataclass(frozen=True)
class FoundFile:
    path: Path
    format_name: str
    size: int
    label: str


@dataclass(frozen=True)
class FoundRow:
    path: str
    format_name: str
    records: int
    size: int


@dataclass
class ScanResult:
    found: list[FoundFile]
    skipped: list[SkipNote]


def is_skipped_dirname(name: str) -> bool:
    """True for a directory the scan does not enter."""
    return name.casefold() in _SKIP_FOLDED


def is_candidate_name(name: str) -> bool:
    """True when the suffix is one the scan will open."""
    return PurePath(name).suffix.lower() in CANDIDATE_SUFFIXES


def depth_below(path: PurePath, root: PurePath) -> int:
    """Path components under ``root``. A file in ``root`` is depth 1."""
    return len(path.relative_to(root).parts)


def is_within(path: PurePath, parent: PurePath) -> bool:
    """True when ``path`` is ``parent`` or a descendant. Not a string prefix."""
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def is_output_directory(directory: PurePath, root: PurePath, out_dir: PurePath) -> bool:
    """True for the report folder, unless that folder is the scan root itself."""
    if directory == root:
        return False
    if directory.name.casefold() == OUTPUT_DIR_NAME.casefold():
        return True
    return directory == out_dir


def relative_display(path: PurePath, root: PurePath) -> str:
    """Path under ``root``, with forward slashes. Absolute when it is outside."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return path.as_posix()
    shown = relative.as_posix()
    return shown or "."


def format_label(format_name: str) -> str:
    return FORMAT_LABELS.get(format_name, format_name)


def format_found_table(rows: Sequence[FoundRow]) -> str:
    """Aligned text table: path, detected format, records, size in bytes."""
    if not rows:
        return ""
    prepared = [
        (
            terminal_safe(row.path),
            format_label(row.format_name),
            str(row.records),
            str(row.size),
        )
        for row in rows
    ]
    headers = ("path", "format", "records", "size")
    widths = [len(header) for header in headers]
    for cells in prepared:
        for index, cell in enumerate(cells):
            widths[index] = max(widths[index], len(cell))

    def line(cells: tuple[str, ...]) -> str:
        padded = [cell.ljust(widths[index]) for index, cell in enumerate(cells)]
        return "  ".join(padded).rstrip()

    body = [line(headers), line(tuple("-" * width for width in widths))]
    body.extend(line(cells) for cells in prepared)
    return "\n".join(body)


def format_skipped(notes: Sequence[SkipNote]) -> str:
    if not notes:
        return ""
    lines = ["skipped:"]
    lines.extend(f"  {terminal_safe(note.path)}: {terminal_safe(note.reason)}" for note in notes)
    return "\n".join(lines)


def scan_directories(
    roots: Sequence[Path],
    *,
    max_depth: int,
    max_file_size: int,
    out_dir: Path,
) -> ScanResult:
    """Scan each root. Nested symlinks are not followed."""
    if max_depth < 0:
        raise UsageError("--max-depth must be >= 0")
    if max_file_size < 0:
        raise UsageError("--max-file-size must be >= 0")
    found: list[FoundFile] = []
    skipped: list[SkipNote] = []
    for root in roots:
        part = _scan_one(
            root,
            max_depth=max_depth,
            max_file_size=max_file_size,
            out_dir=out_dir,
        )
        found.extend(part.found)
        skipped.extend(part.skipped)
    found.sort(key=lambda item: item.label)
    skipped.sort(key=lambda item: item.path)
    return ScanResult(found=found, skipped=skipped)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _scan_one(
    root: Path,
    *,
    max_depth: int,
    max_file_size: int,
    out_dir: Path,
) -> ScanResult:
    found: list[FoundFile] = []
    skipped: list[SkipNote] = []
    seen_inodes: set[tuple[int, int]] = set()
    abs_root = _absolute(root)
    abs_out = _absolute(out_dir)

    def label(path: Path) -> str:
        return relative_display(_absolute(path), abs_root)

    def visit(directory: Path) -> None:
        try:
            info = os.lstat(directory)
        except OSError as exc:
            skipped.append(SkipNote(label(directory), f"unreadable: {exc.strerror or exc}"))
            return
        if info.st_ino != 0:
            key = (info.st_dev, info.st_ino)
            if key in seen_inodes:
                skipped.append(SkipNote(label(directory), "already visited"))
                return
            seen_inodes.add(key)
        try:
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda entry: entry.name)
        except OSError as exc:
            skipped.append(SkipNote(label(directory), f"unreadable: {exc.strerror or exc}"))
            return
        for entry in entries:
            path = Path(entry.path)
            try:
                is_link = entry.is_symlink()
            except OSError as exc:
                skipped.append(SkipNote(label(path), f"unreadable: {exc.strerror or exc}"))
                continue
            if _skip_directory(entry.name, path, is_link=is_link):
                continue
            if is_link:
                _note_symlink(entry, path)
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError as exc:
                skipped.append(SkipNote(label(path), f"unreadable: {exc.strerror or exc}"))
                continue
            if is_dir:
                _enter(path)
                continue
            _consider_file(entry, path)

    def _skip_directory(name: str, path: Path, *, is_link: bool) -> bool:
        try:
            is_dir = Path(path).is_dir() if is_link else os.path.isdir(path)
        except OSError:
            is_dir = False
        # A symlink is not entered either way. A skipped name is recorded once.
        if is_skipped_dirname(name) and (is_dir or is_link):
            skipped.append(SkipNote(label(path), "skipped directory"))
            return True
        return False

    def _note_symlink(entry: os.DirEntry[str], path: Path) -> None:
        try:
            points_at_dir = entry.is_dir(follow_symlinks=True)
        except OSError:
            points_at_dir = True
        if points_at_dir or is_candidate_name(entry.name):
            skipped.append(SkipNote(label(path), "symlink"))

    def _enter(path: Path) -> None:
        if is_output_directory(_absolute(path), abs_root, abs_out):
            skipped.append(SkipNote(label(path), "output folder"))
            return
        try:
            child_depth = depth_below(_absolute(path), abs_root)
        except ValueError:
            skipped.append(SkipNote(label(path), "outside the scan root"))
            return
        if child_depth >= max_depth:
            skipped.append(SkipNote(label(path), f"below --max-depth {max_depth}"))
            return
        visit(path)

    def _consider_file(entry: os.DirEntry[str], path: Path) -> None:
        if not is_candidate_name(entry.name):
            return
        try:
            file_depth = depth_below(_absolute(path), abs_root)
        except ValueError:
            skipped.append(SkipNote(label(path), "outside the scan root"))
            return
        if file_depth > max_depth:
            skipped.append(SkipNote(label(path), f"below --max-depth {max_depth}"))
            return
        try:
            size = entry.stat(follow_symlinks=False).st_size
        except OSError as exc:
            skipped.append(SkipNote(label(path), f"unreadable: {exc.strerror or exc}"))
            return
        if size > max_file_size:
            skipped.append(SkipNote(label(path), f"larger than {max_file_size} bytes"))
            return
        try:
            reader = select_reader(path, None, trust_name=False)
        except ReaderError as exc:
            skipped.append(SkipNote(label(path), _reason(exc, path)))
            return
        except OSError as exc:
            skipped.append(SkipNote(label(path), f"unreadable: {exc.strerror or exc}"))
            return
        found.append(FoundFile(path=path, format_name=reader.name, size=size, label=label(path)))

    visit(abs_root)
    return ScanResult(found=found, skipped=skipped)


def _reason(exc: ReaderError, path: Path) -> str:
    text = str(exc)
    prefix = f"{path}: "
    if text.startswith(prefix):
        return text[len(prefix) :]
    return text
