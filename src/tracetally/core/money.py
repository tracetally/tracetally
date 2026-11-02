# SPDX-License-Identifier: Apache-2.0
"""Decimal cost math. Floats are not used for money."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, model_validator

_PER_MILLION = Decimal(1_000_000)


class Range(BaseModel):
    """Low / mid / high dollar range. Used by findings in later milestones."""

    model_config = ConfigDict(frozen=True)

    low: Decimal
    mid: Decimal
    high: Decimal

    @model_validator(mode="after")
    def _ordered(self) -> Range:
        if not (self.low <= self.mid <= self.high):
            raise ValueError("range must satisfy low <= mid <= high")
        return self


def decimal_price(value: object) -> Decimal:
    """Parse a price that arrived as a YAML string or number.

    Strings are preferred in the price file so binary float cannot sneak in.
    ``Decimal(str(value))`` is used for numbers because ``str(0.15) == "0.15"``.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"price must be a number or string, got {type(value).__name__}")
    try:
        parsed = Decimal(str(value).strip())
    except Exception as exc:
        raise ValueError(f"invalid price {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"price must be finite, got {value!r}")
    return parsed


def token_cost(tokens: int, price_per_million: Decimal) -> Decimal:
    """Cost of ``tokens`` at a per-1M-token price."""
    if tokens < 0:
        raise ValueError("token counts must be >= 0")
    return Decimal(tokens) * price_per_million / _PER_MILLION


def money_text(amount: Decimal | None) -> str | None:
    """Compact decimal for JSON. ``None`` stays ``None``."""
    if amount is None:
        return None
    text = format(amount.quantize(Decimal("0.000000000001")), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_usd(amount: Decimal | None) -> str:
    """Fixed 6 decimal places, or '-' when the amount is unknown."""
    if amount is None:
        return "-"
    return f"{amount.quantize(Decimal('0.000001'))}"
