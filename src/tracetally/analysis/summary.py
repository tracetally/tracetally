# SPDX-License-Identifier: Apache-2.0
"""Cost, tokens, latency percentiles, and 429/5xx rates."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from io import StringIO

from rich import box
from rich.console import Console
from rich.table import Table

from tracetally import __version__
from tracetally.core.money import format_usd
from tracetally.core.stats import percentiles_or_none, rate
from tracetally.core.store import TraceStore
from tracetally.pricing.table import ModelPrice, PriceTable

SUMMARY_SCHEMA_VERSION = 1

METHODOLOGY = (
    "Costs are list prices per 1M tokens from the named price table. "
    "Cached tokens use cached_input when published, otherwise the input price. "
    "Reasoning tokens are reported separately and are not added on top of output tokens. "
    "Long-context rates apply only where the table publishes a token threshold. "
    "Unknown models are left unpriced."
)


@dataclass
class LatencySummary:
    n: int
    p50: float | None
    p95: float | None
    p99: float | None

    @property
    def hidden(self) -> bool:
        return self.p50 is None


@dataclass
class GroupSummary:
    provider: str
    model: str
    endpoint: str | None
    calls: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: Decimal | None = None
    priced_calls: int = 0
    unpriced_calls: int = 0
    reported_cost_usd: Decimal | None = None
    durations_ms: list[float] = field(default_factory=list)
    n_429: int = 0
    n_5xx: int = 0
    n_error: int = 0

    def latency(self) -> LatencySummary:
        found = percentiles_or_none(self.durations_ms)
        if found is None:
            return LatencySummary(n=len(self.durations_ms), p50=None, p95=None, p99=None)
        return LatencySummary(n=len(self.durations_ms), p50=found[50], p95=found[95], p99=found[99])


@dataclass
class Summary:
    price_table_id: str
    price_table_retrieved_on: str
    currency: str
    by_model: list[GroupSummary]
    by_endpoint_model: list[GroupSummary]
    warnings: list[str]
    inputs: list[dict[str, object]]
    time_start: datetime | None
    time_end: datetime | None
    calls: int
    requests: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    reasoning_tokens: int
    cost_usd: Decimal | None
    priced_calls: int
    unpriced_calls: int
    reported_cost_usd: Decimal | None
    n_429: int
    n_5xx: int
    n_error: int
    spans: int

    def to_dict(self) -> dict[str, object]:
        return {
            "summary_schema_version": SUMMARY_SCHEMA_VERSION,
            "tracetally_version": __version__,
            "price_table_id": self.price_table_id,
            "price_table_retrieved_on": self.price_table_retrieved_on,
            "currency": self.currency,
            "methodology": METHODOLOGY,
            "inputs": self.inputs,
            "time_span": {
                "start": _iso(self.time_start),
                "end": _iso(self.time_end),
            },
            "totals": {
                "calls": self.calls,
                "requests": self.requests,
                "spans": self.spans,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cached_input_tokens": self.cached_input_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "cost_usd": _money(self.cost_usd),
                "priced_calls": self.priced_calls,
                "unpriced_calls": self.unpriced_calls,
                "reported_cost_usd": _money(self.reported_cost_usd),
            },
            "by_model": [_group_dict(group) for group in self.by_model],
            "by_endpoint_model": [_group_dict(group) for group in self.by_endpoint_model],
            "error_rates": {
                "requests": self.requests,
                "n_429": self.n_429,
                "n_5xx": self.n_5xx,
                "n_error": self.n_error,
                "rate_429": _rate(self.n_429, self.requests),
                "rate_5xx": _rate(self.n_5xx, self.requests),
                "rate_error": _rate(self.n_error, self.requests),
            },
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2) + "\n"

    def to_text(self) -> str:
        buffer = StringIO()
        console = Console(
            file=buffer,
            width=168,
            force_terminal=False,
            color_system=None,
            legacy_windows=False,
        )
        console.print(
            f"tracetally {__version__} summary  "
            f"price table {self.price_table_id} (retrieved {self.price_table_retrieved_on})"
        )
        span = f"{_iso(self.time_start) or '-'} to {_iso(self.time_end) or '-'}"
        console.print(
            f"calls {self.calls}  requests {self.requests}  spans {self.spans}  "
            f"priced {self.priced_calls}/{self.calls}  window {span}"
        )
        console.print(
            f"tokens in {self.input_tokens}  cached {self.cached_input_tokens}  "
            f"out {self.output_tokens}  reasoning {self.reasoning_tokens}  "
            f"cost {format_usd(self.cost_usd)} {self.currency}"
        )
        console.print()
        console.print(_model_table(self.by_model))
        console.print()
        console.print(_endpoint_table(self.by_endpoint_model))
        console.print()
        console.print(_error_line(self))
        if self.warnings:
            console.print()
            console.print("Warnings")
            for warning in self.warnings:
                console.print(f"- {warning}")
        console.print()
        console.print(METHODOLOGY)
        console.print("Offline. No network calls. No telemetry.")
        return buffer.getvalue()


def summarize(store: TraceStore, table: PriceTable) -> Summary:
    by_model: dict[tuple[str, ...], GroupSummary] = {}
    by_endpoint: dict[tuple[str, ...], GroupSummary] = {}
    warnings = list(store.warnings)
    unpriced: dict[tuple[str, str], int] = {}
    caveats: set[str] = set()
    missing_batch: set[str] = set()
    total_cost: Decimal | None = None
    total_reported: Decimal | None = None
    priced_calls = 0
    unpriced_calls = 0
    time_start: datetime | None = None
    time_end: datetime | None = None

    for index in range(len(store)):
        provider = table.canonical_provider(store.provider[index])
        model = store.model[index]
        endpoint = store.endpoint[index]
        requests = int(store.request_count[index])
        input_tokens = int(store.input_tokens[index])
        output_tokens = int(store.output_tokens[index])
        cached = int(store.cached_input_tokens[index])
        reasoning = int(store.reasoning_tokens[index])
        price = table.resolve(store.provider[index], model)
        cost, batch_applied = _price_call(
            price,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached,
            is_batch=bool(store.is_batch[index]),
        )
        if price is None:
            unpriced[(provider, model)] = unpriced.get((provider, model), 0) + 1
            unpriced_calls += 1
        else:
            priced_calls += 1
            total_cost = (total_cost or Decimal(0)) + (cost or Decimal(0))
            if price.caveat:
                caveats.add(f"{provider}/{price.model}: {price.caveat}")
            if bool(store.is_batch[index]) and not batch_applied:
                missing_batch.add(provider)
        reported = store.reported_cost_usd[index]
        if reported is not None:
            total_reported = (total_reported or Decimal(0)) + reported
        start = store.start_time[index]
        time_start = start if time_start is None or start < time_start else time_start
        time_end = start if time_end is None or start > time_end else time_end
        http_status = store.http_status_at(index)
        error_type = store.error_type[index]
        is_429 = _is_429(http_status, error_type)
        is_5xx = _is_5xx(http_status, error_type)
        is_error = store.status[index] == "error" or is_429 or is_5xx
        duration = store.duration_at(index)

        for group in (
            _group(by_model, (provider, model), provider, model, None),
            _group(by_endpoint, (endpoint, provider, model), provider, model, endpoint),
        ):
            _accumulate(
                group,
                requests=requests,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached=cached,
                reasoning=reasoning,
                cost=cost,
                priced=price is not None,
                reported=reported,
                duration=duration,
                is_429=is_429,
                is_5xx=is_5xx,
                is_error=is_error,
                weight=requests,
            )

    for (provider, model), count in sorted(unpriced.items()):
        noun = "call" if count == 1 else "calls"
        warnings.append(
            f"unpriced model {provider}/{model} ({count} {noun}); no list price, cost left blank"
        )
    for provider in sorted(missing_batch):
        warnings.append(
            f"provider {provider}: batch calls found but the price table has no batch_discount; "
            "standard rates used"
        )
    warnings.extend(sorted(caveats))

    return Summary(
        price_table_id=table.display_id,
        price_table_retrieved_on=table.retrieved_on.isoformat(),
        currency=table.currency,
        by_model=_sort_groups(list(by_model.values())),
        by_endpoint_model=sorted(
            by_endpoint.values(),
            key=lambda group: (group.endpoint or "", group.provider, group.model),
        ),
        warnings=warnings,
        inputs=[
            {
                "path": item.path,
                "sha256": item.sha256,
                "records": item.records,
                "spans": item.spans,
                "skipped": item.skipped,
            }
            for item in store.inputs
        ],
        time_start=time_start,
        time_end=time_end,
        calls=len(store),
        requests=sum(int(value) for value in store.request_count),
        input_tokens=sum(int(value) for value in store.input_tokens),
        output_tokens=sum(int(value) for value in store.output_tokens),
        cached_input_tokens=sum(int(value) for value in store.cached_input_tokens),
        reasoning_tokens=sum(int(value) for value in store.reasoning_tokens),
        cost_usd=total_cost,
        priced_calls=priced_calls,
        unpriced_calls=unpriced_calls,
        reported_cost_usd=total_reported,
        n_429=sum(group.n_429 for group in by_model.values()),
        n_5xx=sum(group.n_5xx for group in by_model.values()),
        n_error=sum(group.n_error for group in by_model.values()),
        spans=len(store.spans),
    )


def _price_call(
    price: ModelPrice | None,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
    is_batch: bool,
) -> tuple[Decimal | None, bool]:
    if price is None:
        return None, False
    return price.cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        is_batch=is_batch,
    )


def _group(
    groups: dict[tuple[str, ...], GroupSummary],
    key: tuple[str, ...],
    provider: str,
    model: str,
    endpoint: str | None,
) -> GroupSummary:
    found = groups.get(key)
    if found is None:
        found = GroupSummary(provider=provider, model=model, endpoint=endpoint)
        groups[key] = found
    return found


def _accumulate(
    group: GroupSummary,
    *,
    requests: int,
    input_tokens: int,
    output_tokens: int,
    cached: int,
    reasoning: int,
    cost: Decimal | None,
    priced: bool,
    reported: Decimal | None,
    duration: float | None,
    is_429: bool,
    is_5xx: bool,
    is_error: bool,
    weight: int,
) -> None:
    group.calls += 1
    group.requests += requests
    group.input_tokens += input_tokens
    group.output_tokens += output_tokens
    group.cached_input_tokens += cached
    group.reasoning_tokens += reasoning
    if priced and cost is not None:
        group.priced_calls += 1
        group.cost_usd = (group.cost_usd or Decimal(0)) + cost
    else:
        group.unpriced_calls += 1
    if reported is not None:
        group.reported_cost_usd = (group.reported_cost_usd or Decimal(0)) + reported
    if duration is not None:
        group.durations_ms.append(duration)
    if is_429:
        group.n_429 += weight
    elif is_5xx:
        group.n_5xx += weight
    if is_error:
        group.n_error += weight


def _sort_groups(groups: list[GroupSummary]) -> list[GroupSummary]:
    def key(group: GroupSummary) -> tuple[bool, Decimal, str, str]:
        missing = group.cost_usd is None
        return (missing, -(group.cost_usd or Decimal(0)), group.provider, group.model)

    return sorted(groups, key=key)


def _is_429(http_status: int | None, error_type: str | None) -> bool:
    if http_status == 429:
        return True
    if not error_type:
        return False
    text = error_type.strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"429", "rate_limit", "rate_limit_exceeded", "too_many_requests", "ratelimiterror"}:
        return True
    return "rate_limit" in text or "too_many_requests" in text


def _is_5xx(http_status: int | None, error_type: str | None) -> bool:
    if http_status is not None and 500 <= http_status <= 599:
        return True
    if error_type and error_type.strip().isdigit():
        code = int(error_type.strip())
        return 500 <= code <= 599
    return False


def _group_dict(group: GroupSummary) -> dict[str, object]:
    latency = group.latency()
    payload: dict[str, object] = {
        "provider": group.provider,
        "model": group.model,
        "calls": group.calls,
        "requests": group.requests,
        "input_tokens": group.input_tokens,
        "output_tokens": group.output_tokens,
        "cached_input_tokens": group.cached_input_tokens,
        "reasoning_tokens": group.reasoning_tokens,
        "cost_usd": _money(group.cost_usd),
        "priced_calls": group.priced_calls,
        "unpriced_calls": group.unpriced_calls,
        "reported_cost_usd": _money(group.reported_cost_usd),
        "latency_ms": {
            "n": latency.n,
            "p50": _latency(latency.p50),
            "p95": _latency(latency.p95),
            "p99": _latency(latency.p99),
        },
        "n_429": group.n_429,
        "n_5xx": group.n_5xx,
        "n_error": group.n_error,
    }
    if group.endpoint is not None:
        payload["endpoint"] = group.endpoint
    return payload


def _model_table(groups: list[GroupSummary]) -> Table:
    table = Table(title="By model", box=box.ASCII, show_lines=False)
    for column in (
        "Provider",
        "Model",
        "Requests",
        "Input",
        "Cached",
        "Output",
        "Cost USD",
        "n",
        "p50",
        "p95",
        "p99",
        "429",
        "5xx",
    ):
        table.add_column(column, no_wrap=column in {"Provider", "Model"}, overflow="fold")
    for group in groups:
        latency = group.latency()
        table.add_row(
            group.provider,
            group.model,
            str(group.requests),
            str(group.input_tokens),
            str(group.cached_input_tokens),
            str(group.output_tokens),
            format_usd(group.cost_usd),
            str(latency.n),
            _latency_cell(latency.p50, latency.hidden),
            _latency_cell(latency.p95, latency.hidden),
            _latency_cell(latency.p99, latency.hidden),
            str(group.n_429),
            str(group.n_5xx),
        )
    return table


def _endpoint_table(groups: list[GroupSummary]) -> Table:
    table = Table(title="By endpoint x model", box=box.ASCII, show_lines=False)
    for column in (
        "Endpoint",
        "Provider",
        "Model",
        "Requests",
        "Input",
        "Output",
        "Cost USD",
        "n",
        "p50",
        "p95",
        "p99",
        "429",
        "5xx",
    ):
        table.add_column(
            column,
            no_wrap=column in {"Endpoint", "Provider", "Model"},
            overflow="fold",
        )
    for group in groups:
        latency = group.latency()
        table.add_row(
            group.endpoint or "(unknown)",
            group.provider,
            group.model,
            str(group.requests),
            str(group.input_tokens),
            str(group.output_tokens),
            format_usd(group.cost_usd),
            str(latency.n),
            _latency_cell(latency.p50, latency.hidden),
            _latency_cell(latency.p95, latency.hidden),
            _latency_cell(latency.p99, latency.hidden),
            str(group.n_429),
            str(group.n_5xx),
        )
    return table


def _error_line(summary: Summary) -> str:
    return (
        "Error rates  "
        f"429 {_rate(summary.n_429, summary.requests) or '-'} "
        f"({summary.n_429}/{summary.requests})  "
        f"5xx {_rate(summary.n_5xx, summary.requests) or '-'} "
        f"({summary.n_5xx}/{summary.requests})  "
        f"error {_rate(summary.n_error, summary.requests) or '-'} "
        f"({summary.n_error}/{summary.requests})"
    )


def _latency_cell(value: float | None, hidden: bool) -> str:
    if hidden:
        return "n<20"
    return _latency(value) or "-"


def _latency(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:.3f}"


def _money(amount: Decimal | None) -> str | None:
    if amount is None:
        return None
    text = format(amount.quantize(Decimal("0.00000001")), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _rate(numerator: int, denominator: int) -> str | None:
    value = rate(numerator, denominator)
    if value is None:
        return None
    return f"{value:.6f}"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
