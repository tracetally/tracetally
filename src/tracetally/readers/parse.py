# SPDX-License-Identifier: Apache-2.0
"""Shared coercion for reader fields. Invalid values raise ``ValueError``."""

from __future__ import annotations

import re
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


_SLASH_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?$")
_YEAR_SLASH = re.compile(r"^(\d{4})/(\d{2})/(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?$")


def parse_time(value: object, *, field: str = "start_time") -> datetime:
    """Parse a timestamp.

    Accepted forms: ISO-8601 (``T`` or space, ``Z`` or a numeric offset),
    ``YYYY/MM/DD``, unix seconds/milliseconds/microseconds/nanoseconds, and
    slash dates whose day and month cannot be swapped. An ambiguous
    ``MM/DD`` vs ``DD/MM`` value is rejected so a date is never guessed.
    Naive values are read as UTC.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, bool):
        raise ValueError(f"{field} is not a timestamp")
    if isinstance(value, (int, float)) or (isinstance(value, str) and _is_number(value)):
        try:
            return _from_unix(value)
        except ValueError as exc:
            raise ValueError(str(exc).replace("start_time", field, 1)) from exc
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is missing")
    text = value.strip()
    iso = _normalize_iso(text)
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        parsed = _parse_slash_date(text, field)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_iso(text: str) -> str:
    """Make a near-ISO timestamp acceptable to ``datetime.fromisoformat`` on 3.10."""
    body = text.strip().replace("Z", "+00:00")
    if "T" not in body and " " in body:
        body = body.replace(" ", "T", 1)
    if "." not in body:
        return body
    head, tail = body.split(".", 1)
    digits = []
    rest = []
    for index, char in enumerate(tail):
        if char.isdigit():
            digits.append(char)
        else:
            rest.append(tail[index:])
            break
    if not digits:
        return body
    fraction = "".join(digits[:6]).ljust(6, "0")
    return head + "." + fraction + "".join(rest)


def _parse_slash_date(text: str, field: str) -> datetime:
    year_first = _YEAR_SLASH.match(text)
    if year_first:
        return _assemble_date(year_first, year=1, month=2, day=3, field=field)
    slash = _SLASH_DATE.match(text)
    if not slash:
        raise ValueError(f"{field} is not an ISO-8601 timestamp")
    first = int(slash.group(1))
    second = int(slash.group(2))
    if first > 12 and second > 12:
        raise ValueError(f"{field} is not an ISO-8601 timestamp")
    if first > 12:
        return _assemble_date(slash, year=3, month=2, day=1, field=field)
    if second > 12:
        return _assemble_date(slash, year=3, month=1, day=2, field=field)
    raise ValueError(f"{field} is ambiguous; use ISO-8601")


def _assemble_date(
    match: re.Match[str],
    *,
    year: int,
    month: int,
    day: int,
    field: str,
) -> datetime:
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    second = int(match.group(6) or 0)
    try:
        return datetime(
            int(match.group(year)),
            int(match.group(month)),
            int(match.group(day)),
            hour,
            minute,
            second,
            tzinfo=timezone.utc,
        )
    except ValueError as exc:
        raise ValueError(f"{field} is not an ISO-8601 timestamp") from exc


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
    return parse_whole_int(value, field=field, minimum=0)


def parse_whole_int(value: object, *, field: str, minimum: int = 0) -> int:
    """Parse an integer, including CSV values like ``1.0`` or ``1,234``.

    A fractional part that is not all zeros is rejected. Thousands separators
    are accepted only when the cell has commas and no decimal point, so a
    European decimal comma is not guessed.
    """
    if isinstance(value, str):
        text = value.strip()
        if "," in text and "." not in text and re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+", text):
            text = text.replace(",", "")
        if re.fullmatch(r"[+-]?\d+\.\d+", text):
            number = Decimal(text)
            if number != number.to_integral_value():
                raise ValueError(f"{field} is not an integer")
            return parse_int(int(number), field=field, minimum=minimum)
        value = text
    return parse_int(value, field=field, minimum=minimum)


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
