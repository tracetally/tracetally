# SPDX-License-Identifier: Apache-2.0
"""CSV helpers for export readers. The file is streamed; nothing is fetched."""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_CAMEL = re.compile(r"([a-z0-9])([A-Z])")


def normalize_key(key: str) -> str:
    """Fold camelCase, spaces, and punctuation into one comparable token."""
    text = key.strip().lstrip("\ufeff")
    text = text.replace("($)", " ").replace("(s)", " s ")
    text = _CAMEL.sub(r"\1_\2", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def detect_delimiter(header_line: str) -> str:
    """Pick comma, semicolon, or tab from the header. Comma wins a tie."""
    counts = {
        ",": header_line.count(","),
        ";": header_line.count(";"),
        "\t": header_line.count("\t"),
    }
    best = max(counts, key=lambda symbol: (counts[symbol], symbol == ","))
    if counts[best] == 0:
        return ","
    return best


def split_header(header_line: str) -> tuple[str, list[str]]:
    delimiter = detect_delimiter(header_line)
    cells = next(csv.reader([header_line], delimiter=delimiter))
    return delimiter, cells


@contextmanager
def open_csv(path: Path) -> Iterator[tuple[list[str], Iterator[tuple[int, dict[str, str]]]]]:
    """Yield ``(headers, rows)``. ``rows`` is ``(record_number, raw cells)``.

    ``record_number`` starts at 2. A quoted newline stays one record.
    """
    handle = path.open("r", encoding="utf-8-sig", newline="")
    try:
        sample = handle.readline()
        if not sample.strip():
            yield [], iter(())
            return
        delimiter, headers = split_header(sample.rstrip("\r\n"))
        reader = csv.reader(handle, delimiter=delimiter)

        def generate() -> Iterator[tuple[int, dict[str, str]]]:
            for index, cells in enumerate(reader, start=2):
                if not cells or not any(cell.strip() for cell in cells):
                    continue
                row: dict[str, str] = {}
                for column, cell in enumerate(cells):
                    if column >= len(headers):
                        row[f"__extra_{column}"] = cell.strip()
                        continue
                    header = headers[column].strip()
                    if not header or header in row:
                        continue
                    row[header] = cell.strip()
                yield index, row

        yield headers, generate()
    finally:
        handle.close()
