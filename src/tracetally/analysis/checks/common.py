# SPDX-License-Identifier: Apache-2.0
"""Shared arithmetic for checks. No I/O."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from tracetally.core.money import Range
from tracetally.core.schema import Effort, Evidence, Finding, Level
from tracetally.core.store import TraceStore
from tracetally.pricing.table import ModelPrice, PriceTable

_MILLION = Decimal(1_000_000)
_DAY_SECONDS = Decimal(86400)
_MONTH_DAYS = Decimal(30)


def monthly_factor(store: TraceStore) -> tuple[Decimal, list[str]]:
    """Scale observed dollars to 30 days. Floor is 1 day.

    The window is the first call's start to the last call's start. A window
    under 7 days is still scaled, and the caveat is attached to findings.
    """
    if len(store) == 0:
        return Decimal(1), ["No calls; monthly figures are not scaled."]
    start = min(store.start_time)
    end = max(store.start_time)
    days = _span_days(start, end)
    caveats: list[str] = []
    if days < 1:
        days = Decimal(1)
        caveats.append("Observed window is under 1 day; monthly figures use a 1-day floor.")
    elif days < 7:
        caveats.append(
            "Observed window is under 7 days; monthly figures extrapolate a short sample."
        )
    return _MONTH_DAYS / days, caveats


def _span_days(start: datetime, end: datetime) -> Decimal:
    delta = end - start
    seconds = Decimal(delta.days * 86400 + delta.seconds) + (
        Decimal(delta.microseconds) / Decimal(1_000_000)
    )
    if seconds < 0:
        seconds = Decimal(0)
    return seconds / _DAY_SECONDS


def scale(amount: Decimal, factor: Decimal) -> Decimal:
    return amount * factor


def monthly_range(low: Decimal, mid: Decimal, high: Decimal, factor: Decimal) -> Range:
    return Range(low=scale(low, factor), mid=scale(mid, factor), high=scale(high, factor))


def severity_for(mid: Decimal) -> Level:
    if mid >= 100:
        return Level.HIGH
    if mid >= 10:
        return Level.MED
    return Level.LOW


def money_for_tokens(tokens: Decimal, price_per_million: Decimal) -> Decimal:
    if tokens <= 0:
        return Decimal(0)
    return tokens * price_per_million / _MILLION


def call_cost(
    table: PriceTable,
    store: TraceStore,
    index: int,
    *,
    is_batch: bool | None = None,
    model: str | None = None,
    provider: str | None = None,
) -> tuple[Decimal | None, ModelPrice | None]:
    resolved = table.resolve(
        provider if provider is not None else store.provider[index],
        model if model is not None else store.model[index],
    )
    if resolved is None:
        return None, None
    amount, _applied = resolved.cost(
        input_tokens=int(store.input_tokens[index]),
        output_tokens=int(store.output_tokens[index]),
        cached_input_tokens=int(store.cached_input_tokens[index]),
        is_batch=bool(store.is_batch[index]) if is_batch is None else is_batch,
    )
    return amount, resolved


def evidence(
    store: TraceStore,
    indexes: Sequence[int],
    *,
    endpoint: str,
    detail: str,
    limit: int = 20,
) -> Evidence:
    traces: list[str] = []
    seen: set[str] = set()
    for index in indexes:
        trace_id = store.trace_id[index]
        if trace_id and trace_id not in seen:
            seen.add(trace_id)
            traces.append(trace_id)
    traces.sort()
    hidden = max(0, len(traces) - limit)
    shown = traces[:limit]
    text = detail
    if hidden:
        text = f"{detail} {hidden} further trace ids omitted."
    return Evidence(trace_ids=shown, count=len(indexes), endpoint=endpoint, detail=text)


def make_finding(
    *,
    check: str,
    endpoint: str,
    title: str,
    low: Decimal,
    mid: Decimal,
    high: Decimal,
    factor: Decimal,
    effort: Effort,
    quality_risk: Level,
    confidence: Level,
    recommendation: str,
    caveats: list[str],
    evidence_rows: list[Evidence],
    what_if: bool = False,
    severity: Level | None = None,
) -> Finding:
    amounts = monthly_range(low, mid, high, factor)
    return Finding(
        id=f"{check}:{endpoint}",
        check=check,
        title=title,
        severity=severity if severity is not None else severity_for(amounts.mid),
        est_monthly_usd=amounts,
        effort=effort,
        quality_risk=quality_risk,
        confidence=confidence,
        evidence=evidence_rows,
        recommendation=recommendation,
        caveats=caveats,
        what_if=what_if,
    )


def failed_call(store: TraceStore, index: int) -> bool:
    if store.status[index] == "error":
        return True
    http_status = store.http_status_at(index)
    if http_status == 429:
        return True
    return http_status is not None and 500 <= http_status <= 599


def input_price(price: ModelPrice, input_tokens: int) -> Decimal:
    rate, _cached, _output = price.rates(input_tokens)
    return rate


def cache_delta(price: ModelPrice, input_tokens: int) -> Decimal | None:
    """Published input price minus published cached-input price.

    ``None`` when the table has no cached-input price. That is not a guess
    that the cache discount is zero dollars of savings: there is no rate to
    subtract.
    """
    if price.cached_input is None and price.long_cached_input is None:
        return None
    full, cached, _output = price.rates(input_tokens)
    delta = full - cached
    if delta <= 0:
        return None
    return delta


def quantize_tokens(value: float) -> Decimal:
    return Decimal(str(value))
