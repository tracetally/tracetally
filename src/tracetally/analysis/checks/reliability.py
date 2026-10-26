# SPDX-License-Identifier: Apache-2.0
"""Latency tails and 429/5xx rates. Dollars are zero; the signal is the rate."""

from __future__ import annotations

from decimal import Decimal

from tracetally.analysis.checks.common import (
    evidence,
    make_finding,
    monthly_factor,
    quantize_tokens,
)
from tracetally.config import AnalyzeConfig
from tracetally.core.schema import Capability, Effort, Finding, Level
from tracetally.core.stats import percentile_linear, rate
from tracetally.core.store import TraceStore
from tracetally.pricing.table import PriceTable

LATENCY_REQUIRES = frozenset({Capability.LATENCY})
ERROR_REQUIRES = frozenset({Capability.ERRORS})


def latency_tail(store: TraceStore, table: PriceTable, cfg: AnalyzeConfig) -> list[Finding]:
    del table
    params = cfg.params
    factor, window_caveats = monthly_factor(store)
    findings: list[Finding] = []
    groups: dict[tuple[str, str, str], list[int]] = {}
    for index in range(len(store)):
        key = (store.endpoint[index], store.provider[index], store.model[index])
        groups.setdefault(key, []).append(index)
    for (endpoint, provider, model), indexes in sorted(groups.items()):
        durations = [
            duration for index in indexes if (duration := store.duration_at(index)) is not None
        ]
        if len(durations) < params.latency_min_calls:
            continue
        p50 = percentile_linear(durations, 50)
        p99 = percentile_linear(durations, 99)
        if p50 <= 0 or quantize_tokens(p99) <= params.latency_tail_multiple * quantize_tokens(p50):
            continue
        label = f"{endpoint} ({provider}/{model})"
        findings.append(
            make_finding(
                check="latency_tail",
                endpoint=f"{endpoint}|{provider}|{model}",
                title=f"Latency tail on {label}",
                low=Decimal(0),
                mid=Decimal(0),
                high=Decimal(0),
                factor=factor,
                effort=Effort.M,
                quality_risk=Level.LOW,
                confidence=Level.MED,
                recommendation=(
                    f"Inspect the slowest calls on {label}. p99 is more than "
                    f"{params.latency_tail_multiple}× p50."
                ),
                caveats=[
                    "No dollar estimate. Percentiles use the linear method and need at least "
                    f"{params.latency_min_calls} durations.",
                    *window_caveats,
                ],
                evidence_rows=[
                    evidence(
                        store,
                        indexes,
                        endpoint=endpoint,
                        detail=(
                            f"n={len(durations)} p50={p50:.3f}ms p99={p99:.3f}ms "
                            f"on {provider}/{model}."
                        ),
                    )
                ],
                severity=Level.HIGH if p99 > p50 * 10 else Level.MED,
            )
        )
    return findings


def error_rates(store: TraceStore, table: PriceTable, cfg: AnalyzeConfig) -> list[Finding]:
    del table
    params = cfg.params
    factor, window_caveats = monthly_factor(store)
    findings: list[Finding] = []
    groups: dict[tuple[str, str, str], list[int]] = {}
    for index in range(len(store)):
        key = (store.endpoint[index], store.provider[index], store.model[index])
        groups.setdefault(key, []).append(index)
    for (endpoint, provider, model), indexes in sorted(groups.items()):
        requests = sum(int(store.request_count[index]) for index in indexes)
        n_429 = 0
        n_5xx = 0
        for index in indexes:
            weight = int(store.request_count[index])
            http_status = store.http_status_at(index)
            error_type = store.error_type[index]
            if _is_429(http_status, error_type):
                n_429 += weight
            elif _is_5xx(http_status, error_type):
                n_5xx += weight
        rate_429 = rate(n_429, requests)
        rate_5xx = rate(n_5xx, requests)
        high_429 = rate_429 is not None and Decimal(str(rate_429)) > params.rate_429_min
        high_5xx = rate_5xx is not None and Decimal(str(rate_5xx)) > params.rate_5xx_min
        if not high_429 and not high_5xx:
            continue
        label = f"{endpoint} ({provider}/{model})"
        parts: list[str] = []
        if high_429 and rate_429 is not None:
            parts.append(f"429 rate {rate_429:.4%} ({n_429}/{requests}) > {params.rate_429_min}")
        if high_5xx and rate_5xx is not None:
            parts.append(f"5xx rate {rate_5xx:.4%} ({n_5xx}/{requests}) > {params.rate_5xx_min}")
        findings.append(
            make_finding(
                check="error_rates",
                endpoint=f"{endpoint}|{provider}|{model}",
                title=f"Error rate on {label}",
                low=Decimal(0),
                mid=Decimal(0),
                high=Decimal(0),
                factor=factor,
                effort=Effort.S,
                quality_risk=Level.LOW,
                confidence=Level.HIGH,
                recommendation=(
                    f"Look at retries and backoff on {label}. The rate is above the configured "
                    "threshold. This finding does not price the failed tokens; retry_waste does "
                    "that when a later call is in the same trace."
                ),
                caveats=[
                    "No dollar estimate. Rates use request_count as the weight.",
                    *window_caveats,
                ],
                evidence_rows=[
                    evidence(
                        store,
                        indexes,
                        endpoint=endpoint,
                        detail="; ".join(parts) + f" on {provider}/{model}.",
                    )
                ],
                severity=Level.MED,
            )
        )
    return findings


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
