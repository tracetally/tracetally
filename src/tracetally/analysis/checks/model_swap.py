# SPDX-License-Identifier: Apache-2.0
"""Reprice an endpoint at another model. Always a what-if."""

from __future__ import annotations

from decimal import Decimal

from tracetally.analysis.checks.common import call_cost, evidence, make_finding, monthly_factor
from tracetally.config import AnalyzeConfig
from tracetally.core.schema import Capability, Effort, Finding, Level
from tracetally.core.store import TraceStore
from tracetally.pricing.table import PriceTable

REQUIRES = frozenset({Capability.PER_CALL})

_WHAT_IF = "what-if: price only, validate quality with evals"


def model_swap(store: TraceStore, table: PriceTable, cfg: AnalyzeConfig) -> list[Finding]:
    if not cfg.tier_map:
        return []
    factor, window_caveats = monthly_factor(store)
    findings: list[Finding] = []
    grouped = store.indexes().by_endpoint
    for endpoint, target in sorted(cfg.tier_map.items()):
        indexes = grouped.get(endpoint, [])
        if not indexes:
            continue
        delta = Decimal(0)
        unpriced = 0
        sources: set[str] = set()
        observed_models: set[str] = set()
        for index in indexes:
            current, current_price = call_cost(store=store, table=table, index=index)
            provider = target.provider or store.provider[index]
            replacement, replacement_price = call_cost(
                store=store,
                table=table,
                index=index,
                provider=provider,
                model=target.model,
            )
            if (
                current is None
                or replacement is None
                or current_price is None
                or replacement_price is None
            ):
                unpriced += 1
                continue
            if replacement >= current:
                continue
            delta += current - replacement
            observed_models.add(f"{current_price.provider}/{current_price.model}")
            sources.add(replacement_price.source_url)
        if delta <= 0:
            continue
        observed = ", ".join(sorted(observed_models)) or "the observed model"
        target_label = f"{target.provider}/{target.model}" if target.provider else target.model
        caveats = [
            f"{_WHAT_IF}. The range is a single list-price delta (low = mid = high) "
            "because every observed token was repriced. It is not an adoption range and "
            "it does not say the cheaper model is good enough.",
            f"Price table {table.display_id}, retrieved {table.retrieved_on.isoformat()}.",
            *window_caveats,
        ]
        if sources:
            caveats.append("Target price source: " + "; ".join(sorted(sources)) + ".")
        if unpriced:
            caveats.append(f"{unpriced} calls could not be priced at both models and are omitted.")
        findings.append(
            make_finding(
                check="model_swap",
                endpoint=endpoint,
                title=f"what-if: reprice {endpoint} from {observed} to {target_label}",
                low=delta,
                mid=delta,
                high=delta,
                factor=factor,
                effort=Effort.M,
                quality_risk=Level.HIGH,
                confidence=Level.MED,
                recommendation=(
                    f"{_WHAT_IF}. Run an eval on {endpoint} before moving it to {target_label}."
                ),
                caveats=caveats,
                evidence_rows=[
                    evidence(
                        store,
                        indexes,
                        endpoint=endpoint,
                        detail=(
                            f"{len(indexes)} calls repriced from {observed} to {target_label}. "
                            "Same token counts, including cached tokens and batch flags."
                        ),
                    )
                ],
                what_if=True,
            )
        )
    return findings
