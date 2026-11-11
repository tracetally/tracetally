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


def _write_artifacts(argv: list[str]) -> None:
    out = Path(argv[argv.index("--out") + 1])
    out.mkdir(parents=True, exist_ok=True)
    for name in ("dashboard.html", "report.md", "findings.json"):
        (out / name).write_text("{}\n", encoding="utf-8")


def test_smoke_runs_offline_file_empty_folder_and_autodetect() -> None:
    module = _smoke()
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        path = Path(argv[-1])
        assert argv[1:4] == ["run", "--offline", "--no-open"]
        assert "--format" not in argv
        if path.name == "empty":
            assert list(path.iterdir()) == []
            return subprocess.CompletedProcess(
                argv, 0, stdout=f"{module.EMPTY_FOLDER_MESSAGE}\n", stderr=""
            )
        if path.name == "mixed":
            names = {item.name for item in path.iterdir()}
            assert names == {"calls.jsonl", "traces.json"}
            assert path.joinpath("calls.jsonl").read_text(encoding="utf-8").strip()
            assert path.joinpath("traces.json").read_text(encoding="utf-8").strip()
        else:
            assert path.is_file()
            assert path.suffix == ".jsonl"
            assert path.read_text(encoding="utf-8").strip()
        _write_artifacts(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    binary = Path("/tmp/tt/bin/tracetally")
    module.run_smoke(binary, runner=runner)
    assert [Path(call[-1]).name for call in calls] == ["calls.jsonl", "empty", "mixed"]
    assert all(Path(call[0]) == binary for call in calls)


def test_empty_folder_must_print_the_message_and_exit_0() -> None:
    module = _smoke()

    def quiet(argv: list[str]) -> subprocess.CompletedProcess[str]:
        path = Path(argv[-1])
        if path.name == "empty":
            return subprocess.CompletedProcess(argv, 0, stdout="nothing to see\n", stderr="")
        _write_artifacts(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    try:
        module.run_smoke(Path("tracetally"), runner=quiet)
    except SystemExit as exc:
        assert module.EMPTY_FOLDER_MESSAGE in str(exc)
    else:
        raise AssertionError("empty folder output was accepted")

    def failed(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 2, stdout="", stderr="boom")

    try:
        module.run_smoke(Path("tracetally"), runner=failed)
    except SystemExit as exc:
        assert "exited 2" in str(exc)
    else:
        raise AssertionError("non-zero run was accepted")


def test_explicit_file_must_write_the_report_artifacts() -> None:
    module = _smoke()

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        path = Path(argv[-1])
        if path.name != "calls.jsonl":
            if path.name != "empty":
                _write_artifacts(list(argv))
            stdout = f"{module.EMPTY_FOLDER_MESSAGE}\n" if path.name == "empty" else ""
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    try:
        module.run_smoke(Path("tracetally"), runner=runner)
    except SystemExit as exc:
        assert "dashboard.html" in str(exc)
    else:
        raise AssertionError("missing report artifacts were accepted")


def test_ci_smokes_run_offline_without_committed_examples() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "clean_wheel_smoke.py").read_text(encoding="utf-8")
    assert "scripts/clean_wheel_smoke.py" in text
    assert "scripts/check_package_contents.py" in text
    assert "scripts/check_no_http_clients.py" in text
    assert "tracetally summary" not in text
    assert "tracetally demo" not in text
    assert "examples/synthetic" not in text
    assert "--offline" in script
    assert "--no-open" in script
    assert "dashboard.html" in script
    assert "telemetry" not in text
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "scripts/clean_wheel_smoke.py" in release
    assert "examples/synthetic" not in release
    assert "tracetally summary" not in release
    assert "environment:" in release
    assert "id-token: write" in release


def test_ci_matrix_follows_the_public_full_policy() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "workflow_dispatch:" in text
    assert "full_matrix:" in text
    assert "type: boolean" in text
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in text
    assert "github.event_name" in text
    assert "branches:\n      - main" in text
    assert 'python-version=["3.10","3.13"]' in text
    assert 'os=["ubuntu-latest","macos-latest","windows-latest"]' in text
    assert 'python-version=["3.10","3.11","3.12","3.13"]' in text
    assert "repository.private" in text
    assert 'if [ "$PRIVATE" = "false" ]' in text
    assert "github.event.repository.visibility" not in text
