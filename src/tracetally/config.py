# SPDX-License-Identifier: Apache-2.0
"""Run configuration supplied by the CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReaderConfig:
    """Per-read options.

    Malformed lines and invalid records are skipped with a warning unless
    ``strict`` is set, in which case the first problem raises ``ReaderError``.
    """

    endpoint_attr: str | None = None
    strict: bool = False
    format_name: str | None = None


@dataclass(frozen=True)
class SummaryConfig:
    paths: tuple[Path, ...]
    reader: ReaderConfig = ReaderConfig()
    prices_path: Path | None = None
    json_output: bool = False
