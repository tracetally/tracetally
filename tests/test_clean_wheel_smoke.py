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


def _ok(argv: list[str]) -> subprocess.CompletedProcess[str]:
    path = Path(argv[-1])
    if path.is_dir():
        return subprocess.CompletedProcess(argv, 0, stdout="No LLM usage file found\n", stderr="")
    return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")


def test_smoke_analyzes_offline_and_leaves_run_disabled(capsys) -> None:
    module = _smoke()
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        path = Path(argv[-1])
        assert path.is_file()
        assert path.suffix == ".jsonl"
        text = path.read_text(encoding="utf-8")
        assert text.strip()
        assert '"model"' in text
        return _ok(list(argv))

    binary = Path("/tmp/tt/bin/tracetally")
    module.run_smoke(binary, runner=runner)
    assert len(calls) == 1
    # Windows stringifies Path with backslashes. Compare the path, not the text.
    assert Path(calls[0][0]) == binary
    assert calls[0][1:3] == ["analyze", "--offline"]
    assert Path(calls[0][-1]).name == "calls.jsonl"
    assert module.RUN_OFFLINE_ENABLED is False
    assert module.EMPTY_FOLDER_ENABLED is False
    assert module.AUTO_DETECT_ENABLED is False
    notes = capsys.readouterr().out
    assert "TODO(B1): tracetally run --offline on the runtime-generated JSONL" in notes
    assert "No LLM usage file found" in notes
    assert "TODO(B2): auto-detect a directory of mixed usage files" in notes
    assert "do not pass a format flag" in notes


def test_enabling_b1_runs_offline_and_requires_the_empty_folder_message(monkeypatch) -> None:
    module = _smoke()
    monkeypatch.setattr(module, "RUN_OFFLINE_ENABLED", True)
    monkeypatch.setattr(module, "EMPTY_FOLDER_ENABLED", True)
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        path = Path(argv[-1])
        if path.is_dir():
            assert list(path.iterdir()) == []
        return _ok(list(argv))

    module.run_smoke(Path("tracetally"), runner=runner)
    assert [call[1:3] for call in calls] == [
        ["analyze", "--offline"],
        ["run", "--offline"],
        ["run", "--offline"],
    ]
    assert Path(calls[0][-1]).name == "calls.jsonl"
    assert Path(calls[1][-1]).name == "calls.jsonl"
    assert Path(calls[2][-1]).name == "empty"

    def quiet(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="nothing to see\n", stderr="")

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
        raise AssertionError("non-zero command was accepted")


def test_auto_detect_stays_disabled_and_fails_closed_when_forced(monkeypatch) -> None:
    module = _smoke()
    monkeypatch.setattr(module, "AUTO_DETECT_ENABLED", True)

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        assert argv[1] == "analyze"
        return _ok(list(argv))

    try:
        module.run_smoke(Path("tracetally"), runner=runner)
    except SystemExit as exc:
        assert "TODO(B2)" in str(exc)
        assert "format flag" in str(exc)
    else:
        raise AssertionError("auto-detect was treated as done")


def test_ci_smokes_analyze_and_disables_run_until_later_prs() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "clean_wheel_smoke.py").read_text(encoding="utf-8")
    assert "scripts/clean_wheel_smoke.py" in text
    assert "scripts/check_package_contents.py" in text
    assert "scripts/check_no_http_clients.py" in text
    assert "analyze --offline" in text
    assert text.count("if: ${{ false }}") == 3
    assert "TODO(B1): tracetally run --offline on the runtime-generated JSONL" in text
    assert "TODO(B1): empty folder prints No LLM usage file found and exits 0" in text
    assert "TODO(B2): auto-detect a directory of mixed usage files, no format flag" in text
    assert "this step fails" not in text
    assert "tracetally summary" not in text
    assert "tracetally demo" not in text
    assert "examples/synthetic" not in text
    assert "RUN_OFFLINE_ENABLED = False" in script
    assert "EMPTY_FOLDER_ENABLED = False" in script
    assert "AUTO_DETECT_ENABLED = False" in script
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
    assert "analyze --offline" in text
    assert text.count("if: ${{ false }}") == 3
