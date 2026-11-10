# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _smoke():
    script = ROOT / "scripts" / "clean_wheel_smoke.py"
    spec = importlib.util.spec_from_file_location("clean_wheel_smoke", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_runs_offline_on_factory_output_and_an_empty_folder() -> None:
    module = _smoke()
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        path = Path(argv[-1])
        if path.is_dir():
            assert list(path.iterdir()) == []
            return subprocess.CompletedProcess(
                argv, 0, stdout=f"{module.EMPTY_FOLDER_MESSAGE}\n", stderr=""
            )
        assert path.is_file()
        assert path.suffix == ".jsonl"
        assert path.read_text(encoding="utf-8").strip()
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    binary = Path("/tmp/tt/bin/tracetally")
    module.run_smoke(binary, runner=runner)
    assert [call[1:3] for call in calls] == [["run", "--offline"], ["run", "--offline"]]
    # Windows stringifies Path with backslashes. Compare the path, not the text.
    assert Path(calls[0][0]) == binary
    assert Path(calls[1][0]) == binary
    assert Path(calls[0][-1]).name == "calls.jsonl"
    assert Path(calls[1][-1]).name == "empty"
    assert calls[0][-1] != calls[1][-1]


def test_empty_folder_must_print_the_message_and_exit_0() -> None:
    module = _smoke()

    def quiet(argv: list[str]) -> subprocess.CompletedProcess[str]:
        path = Path(argv[-1])
        if path.is_dir():
            return subprocess.CompletedProcess(argv, 0, stdout="nothing to see\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    try:
        module.run_smoke(Path("tracetally"), runner=quiet)
    except SystemExit as exc:
        assert module.EMPTY_FOLDER_MESSAGE in str(exc)
    else:
        raise AssertionError("empty folder output was accepted")

    def failed(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 2, stdout="", stderr="No such command 'run'")

    try:
        module.run_smoke(Path("tracetally"), runner=failed)
    except SystemExit as exc:
        assert "exited 2" in str(exc)
    else:
        raise AssertionError("non-zero run was accepted")


def test_ci_smokes_run_and_not_the_demo_examples() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "scripts/clean_wheel_smoke.py" in text
    assert "scripts/check_package_contents.py" in text
    assert "scripts/check_no_http_clients.py" in text
    assert "tracetally summary" not in text
    assert "tracetally demo" not in text
    assert "analyze --offline" not in text
    assert "if: ${{ false }}" not in text
    assert "examples/synthetic" not in text
    assert "auto-detect" in text
    assert "B1" in text
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "scripts/clean_wheel_smoke.py" in release
    assert "examples/synthetic" not in release
    assert "tracetally summary" not in release
    assert "environment:" in release
    assert "id-token: write" in release


def test_ci_matrix_is_trimmed_while_the_repo_is_private() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "workflow_dispatch:" in text
    assert "full_matrix:" in text
    assert "type: boolean" in text
    assert "cancel-in-progress: true" in text
    assert "branches:\n      - main" in text
    assert 'python-version=["3.10","3.13"]' in text
    assert 'os=["ubuntu-latest","macos-latest","windows-latest"]' in text
    assert 'python-version=["3.10","3.11","3.12","3.13"]' in text
    assert "visibility" in text
    assert "refs/heads/main" in text
    assert "Revert this trim once the repo is public" in text
