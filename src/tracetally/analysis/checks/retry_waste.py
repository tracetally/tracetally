# SPDX-License-Identifier: Apache-2.0
"""Failed attempts whose tokens were spent again on a retry."""

from __future__ import annotations

from decimal import Decimal

from tracetally.analysis.checks.common import (
    call_cost,
    evidence,
    failed_call,
    make_finding,
    monthly_factor,
)
from tracetally.config import AnalyzeConfig
from tracetally.core.schema import Capability, Effort, Finding, Level
from tracetally.core.store import TraceStore
from tracetally.pricing.table import PriceTable

REQUIRES = frozenset({Capability.PER_CALL, Capability.ERRORS})


def retry_waste(store: TraceStore, table: PriceTable, cfg: AnalyzeConfig) -> list[Finding]:
    window = cfg.params.retry_window_seconds
    factor, window_caveats = monthly_factor(store)
    by_endpoint: dict[str, list[int]] = {}
    resent: dict[str, int] = {}
    for _trace_id, indexes in store.indexes().by_trace.items():
        ordered = sorted(
            indexes, key=lambda index: (store.start_time[index], store.record_id[index])
        )
        for position, index in enumerate(ordered):
            if not failed_call(store, index):
                continue
            follower = _retry_index(store, ordered, position, index, window)
            if follower is None:
                continue
            endpoint = store.endpoint[index]
            by_endpoint.setdefault(endpoint, []).append(index)
            if _same_context(store, index, follower):
                resent[endpoint] = resent.get(endpoint, 0) + 1
    findings: list[Finding] = []
    for endpoint, failed in sorted(by_endpoint.items()):
        spent = Decimal(0)
        unpriced = 0
        for index in failed:
            amount, price = call_cost(store=store, table=table, index=index)
            if price is None or amount is None:
                unpriced += 1
                continue
            spent += amount
        matched = resent.get(endpoint, 0)
        caveats = [
            "Dollars are the list price of the failed attempts only. The successful retry "
            "is not added. Low, mid, and high match because the tokens were observed, "
            "not assumed.",
            *window_caveats,
        ]
        if unpriced:
            caveats.append(f"{unpriced} failed attempts use an unpriced model and are omitted.")
        findings.append(
            make_finding(
                check="retry_waste",
                endpoint=endpoint,
                title=f"Retry waste on {endpoint}",
                low=spent,
                mid=spent,
                high=spent,
                factor=factor,
                effort=Effort.S,
                quality_risk=Level.LOW,
                confidence=Level.HIGH if matched == len(failed) and failed else Level.MED,
                recommendation=(
                    f"On {endpoint}, stop resending the full prompt after a failure. "
                    "Retry the request only, or cache the prefix, and cap attempt counts."
                ),
                caveats=caveats,
                evidence_rows=[
                    evidence(
                        store,
                        failed,
                        endpoint=endpoint,
                        detail=(
                            f"{len(failed)} failed attempts were followed by another call on "
                            f"the same trace within {window}s or by a higher attempt index. "
                            f"{matched} of them resent the same content hash."
                        ),
                    )
                ],
            )
        )
    return findings


def _retry_index(
    store: TraceStore,
    ordered: list[int],
    position: int,
    index: int,
    window: int,
) -> int | None:
    endpoint = store.endpoint[index]
    attempt = store.attempt_at(index)
    start = store.start_time[index]
    for follower in ordered[position + 1 :]:
        if store.endpoint[follower] != endpoint:
            continue
        later_attempt = store.attempt_at(follower)
        if attempt is not None and later_attempt is not None and later_attempt > attempt:
            return follower
        gap = (store.start_time[follower] - start).total_seconds()
        if 0 <= gap <= window:
            return follower
    return None


def _same_context(store: TraceStore, failed: int, follower: int) -> bool:
    left = store.content_hash[failed]
    right = store.content_hash[follower]
    return bool(left) and left == right
