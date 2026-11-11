# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

import pytest

from tests.support.factories import (
    call_record,
    write_jsonl_records,
    write_langfuse_observations,
    write_openai_usage_csv,
    write_otel_openinference,
    write_pretty_json,
    write_unrecognized,
)
from tracetally.core.errors import ReaderError, UsageError
from tracetally.readers.base import select_reader
from tracetally.readers.discover import (
    DEFAULT_MAX_FILE_SIZE,
    SKIP_DIR_NAMES,
    FoundRow,
    SkipNote,
    depth_below,
    format_found_table,
    is_candidate_name,
    is_output_directory,
    is_skipped_dirname,
    is_within,
    relative_display,
    scan_directories,
)


def test_windows_paths_do_not_use_a_string_prefix() -> None:
    root = PureWindowsPath(r"C:\work\proj")
    report = PureWindowsPath(r"c:\work\proj\tracetally-report")
    inside = PureWindowsPath(r"C:\Work\Proj\tracetally-report\findings.json")
    other = PureWindowsPath(r"C:\work\project\calls.jsonl")
    nested = PureWindowsPath(r"C:\work\proj\a\b\c\d\calls.JSONL")
    assert is_within(inside, report)
    assert not is_within(other, root)
    assert report == PureWindowsPath(r"C:\work\proj\tracetally-report")
    assert depth_below(nested, root) == 5
    assert depth_below(PureWindowsPath(r"C:\work\proj\calls.jsonl"), root) == 1
    assert relative_display(nested, root) == "a/b/c/d/calls.JSONL"
    assert relative_display(other, root) == "C:/work/project/calls.jsonl"
    assert is_candidate_name("calls.JSONL")
    assert is_candidate_name("usage.ndjson")
    assert not is_candidate_name("notes.txt")
    assert is_skipped_dirname("Node_Modules")
    assert is_skipped_dirname(".GIT")
    assert not is_skipped_dirname("node_modules_backup")
    assert is_output_directory(report, root, PureWindowsPath(r"D:\custom"))
    assert not is_output_directory(root, root, root)
    custom = PureWindowsPath(r"C:\work\proj\custom-out")
    assert is_output_directory(custom, root, custom)


def test_suffix_alone_is_not_a_format(tmp_path: Path) -> None:
    path = write_unrecognized(tmp_path)
    with pytest.raises(ReaderError, match="could not detect"):
        select_reader(path, None, trust_name=False)
    assert select_reader(path, None).name == "jsonl"


def test_scan_skip_list_symlinks_depth_and_size(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    write_jsonl_records(root, [call_record()], name="keep.jsonl")
    for name in SKIP_DIR_NAMES:
        write_jsonl_records(root / name, [call_record(record_id=name)], name="hidden.jsonl")
    deep = root / "a" / "b" / "c"
    write_jsonl_records(deep, [call_record(record_id="deep")], name="ok.jsonl")
    write_jsonl_records(deep / "d", [call_record(record_id="too-deep")], name="drop.jsonl")
    write_unrecognized(root, name="notes.jsonl")
    write_unrecognized(root, name="notes.txt")
    copied = write_pretty_json(root)
    upper = root / "Calls.JSON"
    upper.write_bytes(copied.read_bytes())
    ndjson = root / "calls.ndjson"
    ndjson.write_bytes((root / "keep.jsonl").read_bytes())
    try:
        (root / "alias.jsonl").symlink_to(root / "keep.jsonl")
        (root / "loop").symlink_to(root, target_is_directory=True)
        outside = tmp_path / "outside"
        write_jsonl_records(outside, [call_record(record_id="secret")], name="secret.jsonl")
        (root / "linked-dir").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available")
    out = root / "custom-out"
    write_jsonl_records(out, [call_record(record_id="report")], name="calls.jsonl")
    (root / "tracetally-report").mkdir()
    write_jsonl_records(
        root / "tracetally-report",
        [call_record(record_id="old-report")],
        name="old.jsonl",
    )
    scanned = scan_directories(
        [root],
        max_depth=4,
        max_file_size=DEFAULT_MAX_FILE_SIZE,
        out_dir=out,
    )
    labels = {item.label: item.format_name for item in scanned.found}
    assert labels["keep.jsonl"] == "jsonl"
    assert labels["a/b/c/ok.jsonl"] == "jsonl"
    assert labels["Calls.JSON"] == "jsonl"
    assert "a/b/c/d/drop.jsonl" not in labels
    assert "notes.txt" not in labels
    assert "secret.jsonl" not in labels
    assert all("hidden.jsonl" not in label for label in labels)
    assert all(not label.startswith("custom-out") for label in labels)
    assert all(not label.startswith("tracetally-report") for label in labels)
    reasons = {note.path: note.reason for note in scanned.skipped}
    assert reasons["notes.jsonl"].startswith("could not detect")
    assert reasons["alias.jsonl"] == "symlink"
    assert reasons["loop"] == "symlink"
    assert reasons["linked-dir"] == "symlink"
    assert reasons[".git"] == "skipped directory"
    assert reasons["custom-out"] == "output folder"
    assert reasons["tracetally-report"] == "output folder"
    assert reasons["a/b/c/d"] == "below --max-depth 4"
    sized = tmp_path / "sized"
    big = write_jsonl_records(sized, [call_record(record_id="big")], name="big.jsonl")
    limit = big.stat().st_size - 1
    too_big = scan_directories(
        [sized], max_depth=2, max_file_size=limit, out_dir=tmp_path / "elsewhere"
    )
    assert too_big.found == []
    assert too_big.skipped[0].reason == f"larger than {limit} bytes"
    allowed = scan_directories(
        [sized],
        max_depth=2,
        max_file_size=big.stat().st_size,
        out_dir=tmp_path / "elsewhere",
    )
    assert any(item.label == "big.jsonl" for item in allowed.found)
    assert DEFAULT_MAX_FILE_SIZE == 64 * 1024 * 1024


def test_scan_mixes_formats_and_rejects_bad_limits(tmp_path: Path) -> None:
    root = tmp_path / "mix"
    write_pretty_json(root)
    write_otel_openinference(root)
    write_langfuse_observations(root)
    write_openai_usage_csv(root)
    scanned = scan_directories(
        [root], max_depth=4, max_file_size=DEFAULT_MAX_FILE_SIZE, out_dir=root / "out"
    )
    found = {item.label: item.format_name for item in scanned.found}
    assert found["pretty.json"] == "jsonl"
    assert found["openinference.json"] == "otel"
    assert found["observations.json"] == "langfuse"
    assert found["usage.csv"] == "openai_csv"
    table = format_found_table(
        [
            FoundRow(path=item.label, format_name=item.format_name, records=1, size=item.size)
            for item in scanned.found
        ]
    )
    assert "OTLP/OTel" in table
    assert "OpenAI usage CSV" in table
    assert "Langfuse" in table
    with pytest.raises(UsageError, match="--max-depth"):
        scan_directories([root], max_depth=-1, max_file_size=1, out_dir=root)
    with pytest.raises(UsageError, match="--max-file-size"):
        scan_directories([root], max_depth=1, max_file_size=-1, out_dir=root)


def test_scan_reports_unreadable_and_repeated_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    write_jsonl_records(root, [call_record()])
    locked = root / "locked"
    locked.mkdir()
    real_scandir = os.scandir

    def scanning(path: os.PathLike[str] | str) -> os.ScandirIterator:
        if Path(path).name == "locked":
            raise PermissionError(13, "denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr("tracetally.readers.discover.os.scandir", scanning)
    scanned = scan_directories(
        [root], max_depth=4, max_file_size=DEFAULT_MAX_FILE_SIZE, out_dir=root / "out"
    )
    locked_notes = [note for note in scanned.skipped if note.path == "locked"]
    assert locked_notes and locked_notes[0].reason == "unreadable: denied"

    again = root / "again"
    again.mkdir()
    root_stat = os.lstat(root)
    real_lstat = os.lstat

    def same_inode(path: os.PathLike[str] | str) -> os.stat_result:
        if Path(path).name == "again":
            return root_stat
        return real_lstat(path)

    monkeypatch.setattr("tracetally.readers.discover.os.lstat", same_inode)
    repeated = scan_directories(
        [root], max_depth=4, max_file_size=DEFAULT_MAX_FILE_SIZE, out_dir=root / "out"
    )
    again_notes = [note for note in repeated.skipped if note.path == "again"]
    assert again_notes and again_notes[0].reason == "already visited"


def test_ambiguous_sniff_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tracetally.readers.base import BaseReader

    class Alpha(BaseReader):
        name = "alpha"
        capabilities = frozenset()

        @classmethod
        def sniff(cls, path: Path, head: bytes) -> float:
            return 0.8

    class Beta(BaseReader):
        name = "beta"
        capabilities = frozenset()

        @classmethod
        def sniff(cls, path: Path, head: bytes) -> float:
            return 0.8

    monkeypatch.setattr(
        "tracetally.readers.base.registry",
        lambda: {"alpha": Alpha, "beta": Beta},
    )
    path = write_jsonl_records(tmp_path, [call_record()], name="x.json")
    scanned = scan_directories(
        [tmp_path], max_depth=2, max_file_size=DEFAULT_MAX_FILE_SIZE, out_dir=tmp_path / "out"
    )
    assert scanned.found == []
    assert scanned.skipped[0].path == path.name
    assert "ambiguous" in scanned.skipped[0].reason


def test_symlink_root_and_ancestor_are_not_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real"
    write_jsonl_records(real / "child", [call_record(record_id="secret-link")], name="secret.jsonl")
    write_jsonl_records(real, [call_record(record_id="kept")], name="kept.jsonl")
    try:
        link_root = tmp_path / "link-root"
        link_root.symlink_to(real / "child", target_is_directory=True)
        linkparent = tmp_path / "linkparent"
        linkparent.symlink_to(real, target_is_directory=True)
        outside = tmp_path / "outside"
        write_jsonl_records(outside, [call_record(record_id="outside")], name="outside.jsonl")
        (real / "child-link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available")

    real_scandir = os.scandir

    def scanning(path: os.PathLike[str] | str) -> os.ScandirIterator:
        text = os.fspath(path).rstrip("\\/")
        if text.endswith(f"{os.sep}link-root") or text.endswith(f"{os.sep}linkparent{os.sep}child"):
            raise AssertionError(f"opened symlink path {text}")
        return real_scandir(path)

    monkeypatch.setattr("tracetally.readers.discover.os.scandir", scanning)
    out = tmp_path / "out"
    root_scan = scan_directories(
        [link_root], max_depth=4, max_file_size=DEFAULT_MAX_FILE_SIZE, out_dir=out
    )
    assert root_scan.found == []
    assert root_scan.skipped == [SkipNote("link-root", "symlink")]

    ancestor = scan_directories(
        [linkparent / "child"],
        max_depth=4,
        max_file_size=DEFAULT_MAX_FILE_SIZE,
        out_dir=out,
    )
    assert ancestor.found == []
    assert ancestor.skipped == [SkipNote("linkparent", "symlink")]

    direct = scan_directories([real], max_depth=4, max_file_size=DEFAULT_MAX_FILE_SIZE, out_dir=out)
    labels = {item.label for item in direct.found}
    assert "kept.jsonl" in labels
    assert "child/secret.jsonl" in labels
    assert "outside.jsonl" not in labels
    reasons = {note.path: note.reason for note in direct.skipped}
    assert reasons["child-link"] == "symlink"
