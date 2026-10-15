# SPDX-License-Identifier: Apache-2.0
"""Shared coercion for reader fields. Invalid values raise ``ValueError``."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from tracetally.core.schema import Status

_CONTENT_KEYS = frozenset(
    {
        "prompt",
        "messages",
        "input",
        "output",
        "response",
        "content",
        "completion",
        "system",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.system_instructions",
        "gen_ai.prompt",
        "llm.input_messages",
        "llm.output_messages",
        "llm.prompts",
    }
)
_ATTR_ALLOW = frozenset({"env", "project", "feature", "service.name"})


def content_key(name: str) -> bool:
    return name in _CONTENT_KEYS or name.endswith(".prompt") or "message" in name


def parse_time(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, bool):
        raise ValueError("start_time is not a timestamp")
    if isinstance(value, (int, float)) or (isinstance(value, str) and _is_number(value)):
        return _from_unix(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("start_time is missing")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("start_time is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_number(value: str) -> bool:
    text = value.strip()
    if text.startswith("-"):
        text = text[1:]
    return text.isdigit()


def _from_unix(value: object) -> datetime:
    """Convert a unix timestamp in s, ms, us, or ns without using binary floats.

    Nanosecond OTLP values do not fit in a float64 mantissa, so the split is
    integer division and ``datetime`` gets whole seconds plus microseconds.
    """
    number = int(str(value).strip())
    if number < 0:
        raise ValueError("start_time is negative")
    if number > 10**17:
        seconds, rem = divmod(number, 1_000_000_000)
        micros = rem // 1_000
    elif number > 10**14:
        seconds, rem = divmod(number, 1_000_000)
        micros = rem
    elif number > 10**11:
        seconds, rem = divmod(number, 1_000)
        micros = rem * 1_000
    else:
        seconds = number
        micros = 0
    # Each unit split above yields micros in 0..999_999. Clamp anyway so a
    # future threshold change cannot pass an illegal microsecond to datetime.
    if micros > 999_999:  # pragma: no cover
        seconds += micros // 1_000_000
        micros = micros % 1_000_000
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=micros)


def parse_int(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field} is missing")
    try:
        if isinstance(value, str):
            number = int(value.strip())
        elif isinstance(value, int):
            number = value
        elif isinstance(value, float) and value.is_integer():
            number = int(value)
        else:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not an integer") from exc
    if number < minimum:
        raise ValueError(f"{field} must be >= {minimum}")
    return number


def parse_optional_int(value: object, *, field: str) -> int | None:
    if value is None or value == "":
        return None
    return parse_int(value, field=field, minimum=0)


def parse_float(value: object, *, field: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{field} is not a number")
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{field} is not a number") from exc
    if number < 0:
        raise ValueError(f"{field} must be >= 0")
    return number


def parse_decimal(value: object, *, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} is not a decimal") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{field} must be >= 0")
    return number


def parse_status(value: object, *, http_status: int | None, error_type: str | None) -> Status:
    if value is None or value == "":
        if (http_status is not None and http_status >= 400) or error_type:
            return Status.ERROR
        return Status.OK
    text = str(value).strip().lower()
    if text in {"ok", "success", "succeeded", "status_code_ok", "unset", "status_code_unset"}:
        return Status.OK
    if text in {"error", "failed", "failure", "status_code_error"}:
        return Status.ERROR
    raise ValueError("status must be ok or error")


def parse_bool(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    raise ValueError("expected a boolean")


def allowlisted_attrs(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    kept: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key)
        if name in _ATTR_ALLOW and not content_key(name):
            kept[name] = str(value)
    return kept


def text_fragments(value: object) -> list[str]:
    """Pull strings out of a prompt-shaped value so they can be hashed and dropped."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, str):
            if node:
                found.append(node)
            return
        if isinstance(node, dict):
            for key, child in node.items():
                if str(key) in {"role", "type", "name"}:
                    continue
                walk(child)
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(value)
    return found
