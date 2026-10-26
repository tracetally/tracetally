# SPDX-License-Identifier: Apache-2.0
"""Work that could move to a provider batch API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from tracetally.analysis.checks.common import call_cost, evidence, make_finding, monthly_factor
from tracetally.config import AnalyzeConfig
from tracetally.core.schema import Capability, Effort, Finding, Level
from tracetally.core.store import TraceStore
from tracetally.pricing.table import PriceTable

REQUIRES = frozenset({Capability.PER_CALL})


def batch_candidates(store: TraceStore, table: PriceTable, cfg: AnalyzeConfig) -> list[Finding]:
    params = cfg.params
    factor, window_caveats = monthly_factor(store)
    findings: list[Finding] = []
    for endpoint, indexes in sorted(store.indexes().by_endpoint.items()):
        eligible = [index for index in indexes if not store.is_batch[index]]
        if not eligible:
            continue
        tagged = _tagged_non_interactive(store, indexes)
        no_identity = all(
            not store.user_id[index] and not store.session_id[index] for index in indexes
        )
        clustered = _recurring_share(store, indexes) >= params.batch_cluster_share
        heuristic = no_identity and clustered
        if not tagged and not heuristic:
            continue
        delta = Decimal(0)
        unpriced = 0
        no_discount = 0
        sources: set[str] = set()
        modes: set[str] = set()
        for index in eligible:
            standard, price = call_cost(store=store, table=table, index=index, is_batch=False)
            if price is None or standard is None:
                unpriced += 1
                continue
            if price.batch_discount is None:
                no_discount += 1
                continue
            batched, applied_price = call_cost(store=store, table=table, index=index, is_batch=True)
            if batched is None or applied_price is None:
                no_discount += 1
                continue
            if batched >= standard:
                continue
            delta += standard - batched
            modes.add(applied_price.batch_cached)
            provider = table.canonical_provider(store.provider[index])
            bucket = table.providers.get(provider)
            if bucket is not None and bucket.batch_source_url:
                sources.add(bucket.batch_source_url)
        if delta <= 0:
            continue
        low, mid, high = params.batch_eligible
        how = (
            "endpoint is tagged non-interactive"
            if tagged
            else (
                "no user or session id, and at least 80% of calls sit in an hour-of-day that recurs"
            )
        )
        caveats = [
            "Savings are list price minus batch price per eligible call, then 50/75/100% of "
            "those calls are assumed movable. The batch discount is not a flat 50% of the "
            "whole cost: cached tokens follow the price file's batch_cached rule "
            f"({', '.join(sorted(modes)) or 'standard'}).",
            "Batch APIs are asynchronous. This does not say the endpoint's latency budget "
            "allows it.",
            *window_caveats,
        ]
        if sources:
            caveats.append("Batch discount source: " + "; ".join(sorted(sources)) + ".")
        if unpriced:
            caveats.append(f"{unpriced} calls use an unpriced model and are omitted.")
        if no_discount:
            caveats.append(f"{no_discount} calls have no published batch discount and are omitted.")
        findings.append(
            make_finding(
                check="batch_candidates",
                endpoint=endpoint,
                title=f"Batch candidate on {endpoint}",
                low=delta * low,
                mid=delta * mid,
                high=delta * high,
                factor=factor,
                effort=Effort.M,
                quality_risk=Level.LOW if tagged else Level.MED,
                confidence=Level.HIGH if tagged else Level.MED,
                recommendation=(
                    f"Move the non-interactive calls on {endpoint} to the provider batch API "
                    "and keep interactive traffic on the synchronous API."
                ),
                caveats=caveats,
                evidence_rows=[
                    evidence(
                        store,
                        eligible,
                        endpoint=endpoint,
                        detail=(
                            f"{len(eligible)}/{len(indexes)} calls are not already batch. {how}."
                        ),
                    )
                ],
            )
        )
    return findings


def _tagged_non_interactive(store: TraceStore, indexes: list[int]) -> bool:
    flags = [store.interactive_at(index) for index in indexes]
    return bool(flags) and all(flag is False for flag in flags)


def _recurring_share(store: TraceStore, indexes: list[int]) -> Decimal:
    """Share of calls whose UTC hour-of-day appears on at least two dates."""
    by_hour: dict[int, set[date]] = {}
    times: list[datetime] = [store.start_time[index] for index in indexes]
    for stamp in times:
        by_hour.setdefault(stamp.hour, set()).add(stamp.date())
    recurring = {hour for hour, days in by_hour.items() if len(days) >= 2}
    if not recurring or not times:
        return Decimal(0)
    hits = sum(1 for stamp in times if stamp.hour in recurring)
    return Decimal(hits) / Decimal(len(times))
