# SPDX-License-Identifier: Apache-2.0
"""Fail if a wheel or sdist ships data files other than the LiteLLM snapshot.

Allowed paths, and only these:

* ``tracetally/pricing/data/model_prices_and_context_window.json`` (wheel)
* ``src/tracetally/pricing/data/model_prices_and_context_window.json`` (sdist)

``.json``, ``.jsonl``, and ``.csv`` anywhere else in the archive fail the check.
The snapshot name is the package's ``BUNDLED_NAME``.
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

SNAPSHOT_NAME = "model_prices_and_context_window.json"
_DATA_SUFFIXES = frozenset({".json", ".jsonl", ".csv"})


def allowed_members() -> set[str]:
    wheel = f"tracetally/pricing/data/{SNAPSHOT_NAME}"
    return {wheel, f"src/{wheel}"}


def problems(path: Path) -> list[str]:
    """Return human-readable failures for one archive. Empty means the archive is clean."""
    names, sdist = _members(path)
    data = _data_paths(names, sdist=sdist)
    allowed = allowed_members()
    found: list[str] = []
    for name in data:
        if name not in allowed:
            found.append(f"unexpected data file: {name}")
    if not any(name in allowed for name in data):
        found.append(f"missing bundled snapshot {SNAPSHOT_NAME}")
    return found


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: check_package_contents.py DIST...", file=sys.stderr)
        return 2
    failed = False
    seen = False
    for raw in args:
        path = Path(raw)
        if not path.is_file():
            continue
        seen = True
        found = problems(path)
        if found:
            failed = True
            print(f"error: {path}", file=sys.stderr)
            for item in found:
                print(f"error: {item}", file=sys.stderr)
        else:
            print(f"ok {path}")
    if not seen:
        print("error: no archives found", file=sys.stderr)
        return 1
    return 1 if failed else 0


def _members(path: Path) -> tuple[list[str], bool]:
    name = path.name
    if name.endswith(".whl"):
        with zipfile.ZipFile(path) as archive:
            return archive.namelist(), False
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        with tarfile.open(path, "r:gz") as archive:
            return archive.getnames(), True
    raise SystemExit(f"error: unsupported archive {path}")


def _data_paths(names: list[str], *, sdist: bool) -> list[str]:
    relative = _strip_sdist_root(names) if sdist else names
    found: list[str] = []
    for name in relative:
        if name.endswith("/"):
            continue
        if PurePosixPath(name).suffix.lower() in _DATA_SUFFIXES:
            found.append(name)
    return found


def _strip_sdist_root(names: list[str]) -> list[str]:
    roots: set[str] = set()
    for name in names:
        parts = PurePosixPath(name).parts
        if parts:
            roots.add(parts[0])
    if len(roots) != 1:
        return names
    root = next(iter(roots))
    prefix = root + "/"
    stripped: list[str] = []
    for name in names:
        if name == root:
            continue
        if name.startswith(prefix):
            stripped.append(name[len(prefix) :])
        else:
            stripped.append(name)
    return stripped


if __name__ == "__main__":
    raise SystemExit(main())
