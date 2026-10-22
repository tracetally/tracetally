# SPDX-License-Identifier: Apache-2.0
"""Say which checks cannot run, and which fields would unblock them."""

from __future__ import annotations

from decimal import Decimal

from tracetally.analysis.checks.common import make_finding, monthly_factor
from tracetally.config import AnalyzeConfig
from tracetally.core.schema import Capability, Effort, Evidence, Finding, Level
from tracetally.core.store import TraceStore
from tracetally.pricing.table import PriceTable

REQUIRES: frozenset[Capability] = frozenset()

_FIELDS = {
    Capability.PER_CALL: "one row per LLM call with model, input_tokens, and output_tokens",
    Capability.LATENCY: "duration_ms or span start and end timestamps",
    Capability.TRACE_TREE: "trace_id and parent_id so retries and loops can be grouped",
    Capability.PROMPT_HASHES: "prompt text, hashed at read, so repeated prefixes can be compared",
    Capability.ERRORS: "status, http_status, or error_type, including 429 and 5xx",
    Capability.CACHED_TOKENS: "cached_input_tokens (provider cache-read tokens)",
    Capability.TOOL_SPANS: "non-LLM tool spans with a name and, when present, an args hash",
}


def observability_gap(store: TraceStore, table: PriceTable, cfg: AnalyzeConfig) -> list[Finding]:
    del table, cfg
    have = store.capabilities or frozenset()
    missing = [cap for cap in Capability if cap not in have]
    if not missing:
        return []
    factor, window_caveats = monthly_factor(store)
    lines = [f"{cap.value}: {_FIELDS[cap]}" for cap in missing]
    return [
        make_finding(
            check="observability_gap",
            endpoint="(all)",
            title="Trace fields missing for some checks",
            low=Decimal(0),
            mid=Decimal(0),
            high=Decimal(0),
            factor=factor,
            effort=Effort.M,
            quality_risk=Level.LOW,
            confidence=Level.HIGH,
            recommendation=(
                "Log the missing fields on the next export. Trace-level checks stay skipped "
                "until those fields are present."
            ),
            caveats=[
                "This finding has no dollar estimate. It lists fields, not a savings claim.",
                *window_caveats,
            ],
            evidence_rows=[
                Evidence(
                    count=len(store),
                    endpoint="(all)",
                    detail="Missing: " + "; ".join(lines) + ".",
                )
            ],
            severity=Level.MED,
        )
    ]
