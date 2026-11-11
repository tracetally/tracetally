# SPDX-License-Identifier: Apache-2.0
"""Smoke ``tracetally run --offline --no-open`` from a clean wheel install.

Inputs are generated at runtime by ``tests.support.synthetic``. Nothing is
written into the repository. This script does not call ``summary`` or ``demo``
and does not read ``examples/``.

Cases:

* an explicit JSONL file, which must write ``dashboard.html``, ``report.md``,
  and ``findings.json``
* an empty directory, which must print ``No LLM usage file found`` and exit 0
* a directory of mixed usage files, scanned with no format flag
"""

from __future__ import annotations

# Launches the installed console script. This harness does not make an HTTP call.
import json
import subprocess  # noqa: TID251
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMPTY_FOLDER_MESSAGE = "No LLM usage file found"
REPORT_FILES = ("dashboard.html", "report.md", "findings.json")

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def run_smoke(tracetally: Path, *, runner: Runner | None = None) -> None:
    """Run the offline cases. ``runner`` is the process launcher, for tests."""
    execute = runner or _default_runner
    with tempfile.TemporaryDirectory(prefix="tracetally-wheel-") as raw:
        work = Path(raw)
        text = _factory_jsonl()
        if not text.strip():
            raise SystemExit("error: test factory produced an empty trace")
        generated = work / "calls.jsonl"
        generated.write_text(text, encoding="utf-8", newline="\n")
        report = work / "report"
        _invoke(execute, tracetally, generated, out=report)
        _require_artifacts(report)

        empty = work / "empty"
        empty.mkdir()
        completed = _invoke(execute, tracetally, empty, out=work / "empty-report")
        _require_empty_message(completed)

        mixed = work / "mixed"
        mixed.mkdir()
        (mixed / "calls.jsonl").write_text(text, encoding="utf-8", newline="\n")
        (mixed / "traces.json").write_text(_factory_otel(), encoding="utf-8", newline="\n")
        mixed_report = work / "mixed-report"
        _invoke(execute, tracetally, mixed, out=mixed_report)
        _require_artifacts(mixed_report)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: clean_wheel_smoke.py /path/to/tracetally", file=sys.stderr)
        return 2
    run_smoke(Path(args[0]))
    return 0


def _load_factory() -> tuple[str, str]:
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from tests.support.synthetic import jsonl_text, otel_document

    return jsonl_text(), json.dumps(otel_document()) + "\n"


def _factory_jsonl() -> str:
    return _load_factory()[0]


def _factory_otel() -> str:
    return _load_factory()[1]


def _default_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), check=False, capture_output=True, text=True)


def _invoke(
    execute: Runner,
    tracetally: Path,
    path: Path,
    *,
    out: Path,
) -> subprocess.CompletedProcess[str]:
    argv = [
        str(tracetally),
        "run",
        "--offline",
        "--no-open",
        "--out",
        str(out),
        str(path),
    ]
    completed = execute(argv)
    _show(completed)
    if completed.returncode != 0:
        detail = f"{completed.stdout or ''}{completed.stderr or ''}"
        raise SystemExit(f"error: {' '.join(argv)} exited {completed.returncode}\n{detail}")
    return completed


def _require_artifacts(out: Path) -> None:
    missing = [
        name
        for name in REPORT_FILES
        if not (out / name).is_file() or (out / name).stat().st_size == 0
    ]
    if missing:
        raise SystemExit(f"error: report {out} is missing {', '.join(missing)}")


def _require_empty_message(completed: subprocess.CompletedProcess[str]) -> None:
    combined = f"{completed.stdout or ''}{completed.stderr or ''}"
    if EMPTY_FOLDER_MESSAGE not in combined:
        raise SystemExit(
            f"error: empty folder must print {EMPTY_FOLDER_MESSAGE!r} and exit 0\n{combined}"
        )


def _show(completed: subprocess.CompletedProcess[str]) -> None:
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(
            completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr
        )


if __name__ == "__main__":
    raise SystemExit(main())
