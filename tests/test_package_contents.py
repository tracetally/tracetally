# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

from tracetally.pricing.catalog import BUNDLED_NAME

ROOT = Path(__file__).resolve().parents[1]


def _checker():
    script = ROOT / "scripts" / "check_package_contents.py"
    spec = importlib.util.spec_from_file_location("check_package_contents", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snapshot_name_matches_the_package() -> None:
    assert _checker().SNAPSHOT_NAME == BUNDLED_NAME


def test_wheel_rejects_extra_data_files(tmp_path: Path) -> None:
    module = _checker()
    wheel = tmp_path / "tracetally-0.1.0.dev0-py3-none-any.whl"
    snapshot = f"tracetally/pricing/data/{BUNDLED_NAME}"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(snapshot, b"{}\n")
        archive.writestr("tracetally/demo/sample.json", b"{}\n")
        archive.writestr("tracetally/readers/usage.csv", b"a,b\n")
        archive.writestr("tracetally/__init__.py", b"")
    assert module.problems(wheel) == [
        "unexpected data file: tracetally/demo/sample.json",
        "unexpected data file: tracetally/readers/usage.csv",
    ]
    assert module.main([str(wheel)]) == 1


def test_sdist_allows_only_the_snapshot(tmp_path: Path) -> None:
    module = _checker()
    sdist = tmp_path / "tracetally-0.1.0.dev0.tar.gz"
    root = "tracetally-0.1.0.dev0"
    snapshot = f"{root}/src/tracetally/pricing/data/{BUNDLED_NAME}"
    with tarfile.open(sdist, "w:gz") as archive:
        _add(archive, snapshot, b"{}\n")
        _add(archive, f"{root}/examples/synthetic.jsonl", b"{}\n")
        _add(archive, f"{root}/tests/fixtures/otel/legacy.json", b"{}\n")
        _add(archive, f"{root}/README.md", b"hello\n")
    assert module.problems(sdist) == [
        "unexpected data file: examples/synthetic.jsonl",
        "unexpected data file: tests/fixtures/otel/legacy.json",
    ]


def test_missing_snapshot_fails(tmp_path: Path) -> None:
    module = _checker()
    wheel = tmp_path / "empty.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("tracetally/__init__.py", b"")
    assert module.problems(wheel) == [f"missing bundled snapshot {BUNDLED_NAME}"]


def test_snapshot_in_the_wrong_directory_is_not_the_exception(tmp_path: Path) -> None:
    module = _checker()
    wheel = tmp_path / "moved.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"tracetally/demo/{BUNDLED_NAME}", b"{}\n")
    found = module.problems(wheel)
    assert any(item.startswith("unexpected data file:") for item in found)
    assert any(item.startswith("missing bundled snapshot") for item in found)


def _add(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    archive.addfile(info, io.BytesIO(payload))
