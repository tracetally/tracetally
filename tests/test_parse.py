# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tracetally.core.money import decimal_price, token_cost
from tracetally.core.stats import percentile_linear, rate
from tracetally.readers.parse import (
    allowlisted_attrs,
    parse_bool,
    parse_decimal,
    parse_float,
    parse_int,
    parse_optional_int,
    parse_status,
    parse_time,
    text_fragments,
)


def test_parse_time_accepts_iso_and_unix_units() -> None:
    aware = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert parse_time(aware) == aware
    naive = datetime(2026, 10, 1, 0, 0, 0)
    assert parse_time(naive).tzinfo is timezone.utc
    assert parse_time("2026-10-01T00:00:00").tzinfo is timezone.utc
    assert parse_time("2026-10-01T00:00:00Z") == aware
    assert parse_time(1_700_000_000).year >= 2023
    assert parse_time(1_700_000_000_000).year >= 2023
    assert parse_time(1_700_000_000_000_000).year >= 2023
    nanos = 1_700_000_000_000_000_000
    parsed = parse_time(str(nanos))
    assert parsed.tzinfo is timezone.utc
    millis = parse_time(1_700_000_000_123)
    assert millis.microsecond == 123_000
    assert parse_time("2026-10-01 00:00:00") == aware
    assert parse_time("2026/10/01 00:00:00") == aware
    assert parse_time("01/13/2026").month == 1
    assert parse_time("13/01/2026").month == 1
    with pytest.raises(ValueError, match="ambiguous"):
        parse_time("10/11/2026")


def test_parse_time_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="timestamp"):
        parse_time(True)
    with pytest.raises(ValueError, match="missing"):
        parse_time("  ")
    with pytest.raises(ValueError, match="ISO-8601"):
        parse_time("yesterday")
    with pytest.raises(ValueError, match="negative"):
        parse_time(-5)
    with pytest.raises(ValueError, match="missing"):
        parse_time(None)


def test_numeric_parsers_name_the_field() -> None:
    assert parse_int(" 4 ", field="input_tokens") == 4
    assert parse_int(3.0, field="input_tokens") == 3
    assert parse_optional_int("", field="cached_input_tokens") is None
    assert parse_float(None, field="duration_ms") is None
    assert parse_float("1.5", field="duration_ms") == pytest.approx(1.5)
    assert parse_decimal("0.01", field="reported_cost_usd") == Decimal("0.01")
    assert parse_decimal(None, field="reported_cost_usd") is None
    with pytest.raises(ValueError, match="input_tokens is missing"):
        parse_int(None, field="input_tokens")
    with pytest.raises(ValueError, match="not an integer"):
        parse_int("nope", field="input_tokens")
    with pytest.raises(ValueError, match="not an integer"):
        parse_int(1.5, field="input_tokens")
    with pytest.raises(ValueError, match="must be >= 1"):
        parse_int(0, field="request_count", minimum=1)
    with pytest.raises(ValueError, match="not a number"):
        parse_float(object(), field="duration_ms")
    with pytest.raises(ValueError, match="not a number"):
        parse_float("nope", field="duration_ms")
    with pytest.raises(ValueError, match="must be >= 0"):
        parse_float(-1, field="duration_ms")
    with pytest.raises(ValueError, match="not a decimal"):
        parse_decimal("nope", field="reported_cost_usd")
    with pytest.raises(ValueError, match="must be >= 0"):
        parse_decimal("-1", field="reported_cost_usd")
    with pytest.raises(ValueError, match="not a decimal"):
        parse_decimal(object(), field="reported_cost_usd")


def test_status_bool_attrs_and_fragments() -> None:
    assert parse_status(None, http_status=500, error_type=None).value == "error"
    assert parse_status("", http_status=None, error_type=None).value == "ok"
    assert parse_status("SUCCESS", http_status=None, error_type=None).value == "ok"
    assert parse_status("failed", http_status=None, error_type=None).value == "error"
    with pytest.raises(ValueError, match="ok or error"):
        parse_status("maybe", http_status=None, error_type=None)
    assert parse_bool(None) is None
    assert parse_bool(True) is True
    assert parse_bool("yes") is True
    assert parse_bool("0") is False
    with pytest.raises(ValueError, match="boolean"):
        parse_bool("maybe")
    assert allowlisted_attrs("nope") == {}
    assert allowlisted_attrs({"env": "prod", "prompt": "secret", "other": "x"}) == {"env": "prod"}
    assert text_fragments(
        [{"role": "user", "content": "hello"}, ["world"], {"type": "text", "name": "skip"}]
    ) == ["hello", "world"]
    assert text_fragments("") == []


def test_money_and_percentile_edges() -> None:
    assert decimal_price(Decimal("1.5")) == Decimal("1.5")
    assert decimal_price(2) == Decimal("2")
    with pytest.raises(ValueError, match="number or string"):
        decimal_price(True)
    with pytest.raises(ValueError, match="invalid price"):
        decimal_price("nope")
    with pytest.raises(ValueError, match="finite"):
        decimal_price("NaN")
    with pytest.raises(ValueError, match=">= 0"):
        token_cost(-1, Decimal("1"))
    assert percentile_linear([3.0], 40) == 3.0
    assert percentile_linear([1.0, 2.0, 3.0], 0) == 1.0
    assert percentile_linear([1.0, 2.0, 3.0], 100) == 3.0
    with pytest.raises(ValueError, match="between 0 and 100"):
        percentile_linear([1.0], -1)
    with pytest.raises(ValueError, match="numerator"):
        rate(-1, 2)
