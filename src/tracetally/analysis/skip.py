# SPDX-License-Identifier: Apache-2.0
"""Which planned checks a run cannot execute.

The checks themselves land in a later milestone. This module only records the
skip when the loaded readers do not provide what the check needs, so a
cost-only export states that instead of looking like a clean bill of health.
"""

from __future__ import annotations

from dataclasses import dataclass

from tracetally.core.schema import Capability, SkippedCheck


@dataclass(frozen=True)
class PlannedCheck:
    check: str
    requires: frozenset[Capability]
    detail: str


# Requirements match the v0.1 check list. Latency percentiles and error rates
# are summary figures with the same capability rule, so a cost-only file skips
# them with a reason instead of printing a zero.
PLANNED_CHECKS: tuple[PlannedCheck, ...] = (
    PlannedCheck(
        "token_bloat",
        frozenset({Capability.PER_CALL}),
        "token bloat compares one call at a time",
    ),
    PlannedCheck(
        "cache_opportunity",
        frozenset({Capability.PROMPT_HASHES}),
        "cache detection needs prompt prefix hashes",
    ),
    PlannedCheck(
        "batch_candidates",
        frozenset({Capability.PER_CALL}),
        "batch detection looks at individual calls",
    ),
    PlannedCheck(
        "retry_waste",
        frozenset({Capability.PER_CALL, Capability.ERRORS}),
        "retry detection needs per-call errors",
    ),
    PlannedCheck(
        "agent_loops",
        frozenset({Capability.TRACE_TREE}),
        "loop detection needs a trace tree",
    ),
    PlannedCheck(
        "model_swap",
        frozenset({Capability.PER_CALL}),
        "a routing what-if reprices individual calls",
    ),
    PlannedCheck(
        "latency_percentiles",
        frozenset({Capability.LATENCY}),
        "percentiles need a duration on each call",
    ),
    PlannedCheck(
        "error_rates",
        frozenset({Capability.ERRORS}),
        "429 and 5xx rates need a status or error type",
    ),
)


def run_mode(capabilities: frozenset[Capability] | None) -> str:
    """``cost-only`` when no input supplies calls, latency, or a trace tree."""
    if capabilities is None:
        return "trace"
    needed = {Capability.PER_CALL, Capability.LATENCY, Capability.TRACE_TREE}
    if needed & set(capabilities):
        return "trace"
    return "cost-only"


def skipped_for(
    capabilities: frozenset[Capability] | None,
    readers: set[str] | frozenset[str] | None = None,
) -> list[SkippedCheck]:
    """Checks whose requirements are not in the intersection of reader capabilities."""
    if capabilities is None:
        return []
    names = set(readers or ())
    skipped: list[SkippedCheck] = []
    for spec in PLANNED_CHECKS:
        missing = tuple(sorted(spec.requires - capabilities, key=lambda cap: cap.value))
        if not missing:
            continue
        joined = ", ".join(cap.value for cap in missing)
        if names == {"openai_csv"}:
            source = "the OpenAI usage CSV is cost-only and does not include"
        elif "openai_csv" in names:
            source = "an input is cost-only and does not include"
        else:
            source = "the loaded inputs do not include"
        skipped.append(
            SkippedCheck(
                check=spec.check,
                reason=f"needs {joined}; {source} {joined}. {spec.detail}",
            )
        )
    return skipped
