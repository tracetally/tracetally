# SPDX-License-Identifier: Apache-2.0
"""User-facing errors. Field and location are part of the message."""

from __future__ import annotations


class TracetallyError(Exception):
    """Base class for errors the CLI turns into a non-zero exit."""


class ReaderError(TracetallyError):
    """A record or file could not be read.

    ``field`` names the missing or invalid field. ``where`` is a stable
    location such as ``spans[3]`` or ``file.jsonl:12``.
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        where: str | None = None,
    ) -> None:
        self.field = field
        self.where = where
        parts: list[str] = []
        if where:
            parts.append(where)
        parts.append(message)
        if field:
            parts.append(f"field {field!r}")
        super().__init__(": ".join(parts))


class PriceError(TracetallyError):
    """The price table is missing, unreadable, or fails validation."""


class UsageError(TracetallyError):
    """The command line is missing something the user must supply."""
