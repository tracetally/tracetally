# SPDX-License-Identifier: Apache-2.0
"""Flag endpoints whose prompts are long relative to the answer."""

from __future__ import annotations

from decimal import Decimal

from tracetally.analysis.checks.common import (
    evidence,
    input_price,
    make_finding,
    money_for_tokens,
    monthly_factor,
    quantize_tokens,
)
from tracetally.config import AnalyzeConfig
from tracetally.core.schema import Capability, Effort, Finding, Level
from tracetally.core.stats import percentile_linear
from tracetally.core.store import TraceStore
from tracetally.pricing.table import PriceTable

REQUIRES = frozenset({Capability.PER_CALL})


def token_bloat(store: TraceStore, table: PriceTable, cfg: AnalyzeConfig) -> list[Finding]:
    params = cfg.params
    factor, window_caveats = monthly_factor(store)
    findings: list[Finding] = []
    for endpoint, indexes in sorted(store.indexes().by_endpoint.items()):
        if len(indexes) < params.bloat_min_calls:
            continue
        inputs = [float(store.input_tokens[index]) for index in indexes]
        output_tokens = sum(int(store.output_tokens[index]) for index in indexes)
        p50 = percentile_linear(inputs, 50)
        p95 = percentile_linear(inputs, 95)
        ratio: Decimal | None = None
        if output_tokens > 0:
            ratio = Decimal(sum(int(value) for value in inputs)) / Decimal(output_tokens)
        ratio_hit = (
            p50 > params.bloat_min_p50_input
            and ratio is not None
            and ratio > params.bloat_min_ratio
        )
        tail_hit = p50 > 0 and quantize_tokens(p95) > params.bloat_tail_multiple * quantize_tokens(
            p50
        )
        if not ratio_hit and not tail_hit:
            continue
        baseline = quantize_tokens(p50)
        excess_cost = Decimal(0)
        unpriced = 0
        excess_calls: list[int] = []
        for index in indexes:
            tokens = Decimal(int(store.input_tokens[index]))
            extra = tokens - baseline
            if extra <= 0:
                continue
            price = table.resolve(store.provider[index], store.model[index])
            if price is None:
                unpriced += 1
                continue
            excess_calls.append(index)
            excess_cost += money_for_tokens(extra, input_price(price, int(tokens)))
        if excess_cost <= 0 and not excess_calls and unpriced == 0:
            continue
        low, mid, high = params.bloat_adoption
        reasons: list[str] = []
        if ratio_hit and ratio is not None:
            reasons.append(
                f"input p50 {baseline} > {params.bloat_min_p50_input} and "
                f"input/output {ratio:.2f} > {params.bloat_min_ratio}"
            )
        if tail_hit:
            reasons.append(
                f"input p95 {quantize_tokens(p95)} > {params.bloat_tail_multiple}× p50 {baseline}"
            )
        caveats = [
            "Savings price tokens above the endpoint input p50 at the uncached input rate. "
            "Adoption 25/50/75% is the low/mid/high range. Cached tokens are not assumed "
            "to be the ones removed.",
            *window_caveats,
        ]
        if unpriced:
            caveats.append(
                f"{unpriced} calls use an unpriced model and are omitted from the dollars."
            )
        findings.append(
            make_finding(
                check="token_bloat",
                endpoint=endpoint,
                title=f"Token bloat on {endpoint}",
                low=excess_cost * low,
                mid=excess_cost * mid,
                high=excess_cost * high,
                factor=factor,
                effort=Effort.S,
                quality_risk=Level.MED,
                confidence=Level.MED if len(indexes) < 100 else Level.HIGH,
                recommendation=(
                    f"Trim {endpoint} context toward the median input size and measure answer "
                    "quality on an eval set before keeping the shorter prompt."
                ),
                caveats=caveats,
                evidence_rows=[
                    evidence(
                        store,
                        excess_calls or indexes,
                        endpoint=endpoint,
                        detail=(
                            f"{'; '.join(reasons)}. "
                            f"{len(indexes)} calls, excess input cost at full adoption "
                            f"priced from the list table."
                        ),
                    )
                ],
            )
        )
    return findings
