# SPDX-License-Identifier: Apache-2.0
"""Repeated tool or model calls inside one trace."""

from __future__ import annotations

from decimal import Decimal

from tracetally.analysis.checks.common import (
    call_cost,
    evidence,
    make_finding,
    monthly_factor,
    quantize_tokens,
)
from tracetally.config import AnalyzeConfig
from tracetally.core.schema import Capability, Effort, Finding, Level
from tracetally.core.stats import percentile_linear
from tracetally.core.store import TraceStore
from tracetally.pricing.table import PriceTable

REQUIRES = frozenset({Capability.TRACE_TREE})


def agent_loops(store: TraceStore, table: PriceTable, cfg: AnalyzeConfig) -> list[Finding]:
    minimum = cfg.params.loop_min_repeats
    factor, window_caveats = monthly_factor(store)
    indexes = store.indexes()
    per_endpoint: dict[str, list[tuple[str, list[int]]]] = {}
    for trace_id, call_indexes in indexes.by_trace.items():
        by_endpoint: dict[str, list[int]] = {}
        for index in call_indexes:
            by_endpoint.setdefault(store.endpoint[index], []).append(index)
        for endpoint, group in by_endpoint.items():
            per_endpoint.setdefault(endpoint, []).append((trace_id, group))

    findings: list[Finding] = []
    for endpoint, traces in sorted(per_endpoint.items()):
        counts = [float(len(group)) for _trace_id, group in traces]
        median = percentile_linear(counts, 50)
        p99 = percentile_linear(counts, 99) if len(traces) >= 20 else None
        looping: list[tuple[str, list[int], str]] = []
        for trace_id, group in traces:
            reasons = _reasons(store, indexes.spans_by_trace.get(trace_id, []), group, minimum, p99)
            if reasons:
                looping.append((trace_id, group, "; ".join(reasons)))
        if not looping:
            continue
        excess_cost = Decimal(0)
        unpriced = 0
        evidence_indexes: list[int] = []
        for _trace_id, group, _reason in looping:
            evidence_indexes.extend(group)
            spent, missing = _excess_cost(store, table, group, median)
            excess_cost += spent
            unpriced += missing
        detail = "; ".join(reason for _trace_id, _group, reason in looping[:8])
        caveats = [
            "Dollars are the list price of LLM calls beyond the endpoint's median calls "
            "per trace, prorated when the median is not a whole number. Low, mid, and high "
            "match because the extra calls were observed. Tool spans are evidence; they are "
            "not priced.",
            *window_caveats,
        ]
        if unpriced:
            caveats.append(f"{unpriced} excess calls use an unpriced model and are omitted.")
        findings.append(
            make_finding(
                check="agent_loops",
                endpoint=endpoint,
                title=f"Agent loop on {endpoint}",
                low=excess_cost,
                mid=excess_cost,
                high=excess_cost,
                factor=factor,
                effort=Effort.M,
                quality_risk=Level.MED,
                confidence=Level.HIGH,
                recommendation=(
                    f"Cap repeated tool and model calls on {endpoint} and stop the trace when "
                    f"the same call repeats {minimum} times."
                ),
                caveats=caveats,
                evidence_rows=[
                    evidence(
                        store,
                        evidence_indexes,
                        endpoint=endpoint,
                        detail=(
                            f"{len(looping)} traces. Median calls per trace is "
                            f"{quantize_tokens(median)}. {detail}."
                        ),
                    )
                ],
            )
        )
    return findings


def _reasons(
    store: TraceStore,
    span_indexes: list[int],
    call_indexes: list[int],
    minimum: int,
    p99: float | None,
) -> list[str]:
    reasons: list[str] = []
    counts: dict[tuple[str, str, str], int] = {}
    hashed = False
    for index in call_indexes:
        name = store.tool_name[index] or store.endpoint[index]
        args = store.args_hash[index] or ""
        if args:
            hashed = True
        key = ("llm", name, args)
        counts[key] = counts.get(key, 0) + 1
    for span_index in span_indexes:
        span = store.spans[span_index]
        args = span.args_hash or ""
        if args:
            hashed = True
        key = (span.kind.value, span.name, args)
        counts[key] = counts.get(key, 0) + 1
    repeated = [item for item in counts.items() if item[1] >= minimum]
    if repeated:
        top = max(repeated, key=lambda item: item[1])
        kind, name, _args = top[0]
        hash_note = "with an args hash" if hashed else "without an args hash"
        reasons.append(f"{kind} {name} repeats {top[1]} times in one trace ({hash_note})")
    if p99 is not None and len(call_indexes) > p99:
        reasons.append(
            f"{len(call_indexes)} LLM calls in one trace is above the endpoint p99 "
            f"({quantize_tokens(p99)})"
        )
    return reasons


def _excess_cost(
    store: TraceStore, table: PriceTable, group: list[int], median: float
) -> tuple[Decimal, int]:
    count = len(group)
    if count == 0:
        return Decimal(0), 0
    excess = Decimal(count) - quantize_tokens(median)
    if excess <= 0:
        return Decimal(0), 0
    spent = Decimal(0)
    priced = 0
    missing = 0
    for index in group:
        amount, price = call_cost(store=store, table=table, index=index)
        if price is None or amount is None:
            missing += 1
            continue
        spent += amount
        priced += 1
    if priced == 0:
        return Decimal(0), missing
    return spent * excess / Decimal(count), missing
