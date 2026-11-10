# SPDX-License-Identifier: Apache-2.0
"""Smoke ``tracetally run --offline`` from a clean wheel install.

The input is generated at runtime by ``tests.support.synthetic.jsonl_text``.
Nothing is written into the repository. This script does not call ``summary``
or ``demo`` and does not read ``examples/``.

B1 adds ``tracetally run``. Until that command exists, this smoke fails.
It still invokes ``run``. ``analyze`` is not a stand-in.

B2 auto-detect is not covered. When that lands, add a third case beside the
explicit file and the empty directory: point ``tracetally run --offline`` at a
directory of mixed usage files and do not pass a format flag.
"""

from __future__ import annotations

# Launches the installed console script. This harness does not make an HTTP call.
import subprocess  # noqa: TID251
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMPTY_FOLDER_MESSAGE = "No LLM usage file found"

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def run_smoke(tracetally: Path, *, runner: Runner | None = None) -> None:
    """Run the two offline cases. ``runner`` is the process launcher, for tests."""
    execute = runner or _default_runner
    with tempfile.TemporaryDirectory(prefix="tracetally-wheel-") as raw:
        work = Path(raw)
        generated = work / "calls.jsonl"
        text = _factory_jsonl()
        if not text.strip():
            raise SystemExit("error: test factory produced an empty trace")
        generated.write_text(text, encoding="utf-8", newline="\n")
        _invoke(execute, tracetally, generated)
        empty = work / "empty"
        empty.mkdir()
        # B2: auto-detect case goes here. Do not add it until that command lands.
        completed = _invoke(execute, tracetally, empty)
        _require_empty_message(completed)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: clean_wheel_smoke.py /path/to/tracetally", file=sys.stderr)
        return 2
    run_smoke(Path(args[0]))
    return 0


def _factory_jsonl() -> str:
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from tests.support.synthetic import jsonl_text

    return jsonl_text()


def _default_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), check=False, capture_output=True, text=True)


def _invoke(
    execute: Runner,
    tracetally: Path,
    path: Path,
) -> subprocess.CompletedProcess[str]:
    argv = [str(tracetally), "run", "--offline", str(path)]
    completed = execute(argv)
    _show(completed)
    if completed.returncode != 0:
        detail = f"{completed.stdout or ''}{completed.stderr or ''}"
        raise SystemExit(f"error: {' '.join(argv)} exited {completed.returncode}\n{detail}")
    return completed


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
