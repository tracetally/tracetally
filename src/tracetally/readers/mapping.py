# SPDX-License-Identifier: Apache-2.0
"""Field picking shared by the export readers."""

from __future__ import annotations

import re
from typing import Any

from tracetally.readers.csvutil import normalize_key
from tracetally.readers.jsonutil import loads

_CODE = re.compile(r"\b([45]\d{2})\b")
_RATE = re.compile(r"rate[_ ]?limit|too many requests", re.IGNORECASE)


def index_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a record onto normalized keys. The first spelling of a key wins."""
    indexed: dict[str, Any] = {}
    for key, value in raw.items():
        name = normalize_key(str(key))
        if name and name not in indexed:
            indexed[name] = value
    return indexed


def first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def as_object(value: object) -> dict[str, Any] | None:
    """Return a dict, parsing a JSON object string. Anything else is ``None``."""
    if isinstance(value, str):
        text = value.strip()
        if not text or text[0] not in "{[":
            return None
        try:
            value = loads(text)
        except (ValueError, TypeError):
            return None
    if isinstance(value, dict):
        return index_fields(value)
    return None


def infer_provider(model: str, explicit: object) -> str:
    """Use an explicit provider when the export has one, otherwise the model id.

    The prefix is only a provider name (``openai``, ``anthropic``, ``gemini``).
    It is not a price. Unknown ids stay ``unknown`` and are left unpriced.
    """
    if explicit is not None:
        text = str(explicit).strip().lower()
        if text:
            return text
    name = model.strip().lower()
    if name.startswith("ft:"):
        return "openai"
    if name.startswith(("gpt-", "chatgpt", "text-embedding-", "o1", "o3", "o4")):
        return "openai"
    if name.startswith("claude"):
        return "anthropic"
    if name.startswith("gemini"):
        return "gemini"
    return "unknown"


def split_field_error(exc: ValueError) -> tuple[str | None, str]:
    """Split ``field: message`` errors raised by the parsers."""
    text = str(exc)
    if ": " in text:
        field, message = text.split(": ", 1)
        if field.isidentifier() or "." in field:
            return field, message
    head = text.split(" ", 1)[0]
    if head.isidentifier():
        return head, text
    return None, text


def status_bits(message: object) -> tuple[int | None, str | None]:
    """Pull an HTTP status out of a status message. The message is kept as the type."""
    if message is None:
        return None, None
    text = str(message).strip()
    if not text:
        return None, None
    code: int | None = None
    match = _CODE.search(text)
    if match:
        code = int(match.group(1))
    elif _RATE.search(text):
        code = 429
    shortened = text if len(text) <= 200 else text[:200]
    return code, shortened
