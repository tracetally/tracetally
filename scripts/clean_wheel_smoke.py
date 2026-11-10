# SPDX-License-Identifier: Apache-2.0
"""Smoke the installed console script from a clean wheel.

The input is generated at runtime by ``tests.support.synthetic.jsonl_text``.
Nothing is written into the repository. This script does not call ``summary``
or ``demo`` and does not read ``examples/``.

Live step:

* ``tracetally analyze --offline`` on that JSONL. Must exit 0.

Disabled until B1 adds ``tracetally run``:

* TODO(B1): ``tracetally run --offline`` on the generated JSONL.
* TODO(B1): ``tracetally run --offline`` on an empty directory. That must
  print ``No LLM usage file found`` and exit 0.

Disabled until B2 adds auto-detect:

* TODO(B2): point the command at a directory of mixed usage files and do not
  pass a format flag.
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

# TODO(B1): set True when `tracetally run` exists.
RUN_OFFLINE_ENABLED = False
# TODO(B1): set True together with RUN_OFFLINE_ENABLED. The empty directory
# must print "No LLM usage file found" and exit 0.
EMPTY_FOLDER_ENABLED = False
# TODO(B2): set True when auto-detect lands. A directory of mixed usage
# files, and no format flag.
AUTO_DETECT_ENABLED = False

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def run_smoke(tracetally: Path, *, runner: Runner | None = None) -> None:
    """Run the live analyze step. B1 and B2 steps stay disabled on this branch."""
    execute = runner or _default_runner
    with tempfile.TemporaryDirectory(prefix="tracetally-wheel-") as raw:
        work = Path(raw)
        generated = work / "calls.jsonl"
        text = _factory_jsonl()
        if not text.strip():
            raise SystemExit("error: test factory produced an empty trace")
        generated.write_text(text, encoding="utf-8", newline="\n")
        _invoke(execute, tracetally, generated, command="analyze")
        _run_offline_step(execute, tracetally, generated)
        empty = work / "empty"
        empty.mkdir()
        _empty_folder_step(execute, tracetally, empty)
        _auto_detect_step(execute, tracetally, work)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: clean_wheel_smoke.py /path/to/tracetally", file=sys.stderr)
        return 2
    run_smoke(Path(args[0]))
    return 0


def _run_offline_step(execute: Runner, tracetally: Path, generated: Path) -> None:
    """TODO(B1): ``tracetally run --offline`` on the runtime-generated JSONL."""
    if not RUN_OFFLINE_ENABLED:
        _disabled("TODO(B1): tracetally run --offline on the runtime-generated JSONL")
        return
    _invoke(execute, tracetally, generated, command="run")


def _empty_folder_step(execute: Runner, tracetally: Path, empty: Path) -> None:
    """TODO(B1): empty directory prints the message and exits 0."""
    if not EMPTY_FOLDER_ENABLED:
        _disabled(
            "TODO(B1): tracetally run --offline on an empty directory must print "
            f"{EMPTY_FOLDER_MESSAGE!r} and exit 0"
        )
        return
    completed = _invoke(execute, tracetally, empty, command="run")
    _require_empty_message(completed)


def _auto_detect_step(execute: Runner, tracetally: Path, work: Path) -> None:
    """TODO(B2): auto-detect a directory of mixed usage files. No format flag."""
    if not AUTO_DETECT_ENABLED:
        _disabled(
            "TODO(B2): auto-detect a directory of mixed usage files; do not pass a format flag"
        )
        return
    # Fail closed. B2's command is not on this branch, so enabling the flag
    # must not turn into a passing no-op. `execute` is the runner B2 will call.
    raise SystemExit(
        "TODO(B2): auto-detect is not implemented. "
        f"Point {tracetally} at {work} (mixed usage files) and do not pass a format flag."
    )


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
    *,
    command: str,
) -> subprocess.CompletedProcess[str]:
    argv = [str(tracetally), command, "--offline", str(path)]
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


def _disabled(message: str) -> None:
    print(message)


def _show(completed: subprocess.CompletedProcess[str]) -> None:
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(
            completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr
        )


if __name__ == "__main__":
    raise SystemExit(main())
