# SPDX-License-Identifier: Apache-2.0
"""Pure checks. Each one declares the reader capabilities it needs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from tracetally.analysis.checks.agent_loops import REQUIRES as AGENT_REQUIRES
from tracetally.analysis.checks.agent_loops import agent_loops
from tracetally.analysis.checks.batch_candidates import REQUIRES as BATCH_REQUIRES
from tracetally.analysis.checks.batch_candidates import batch_candidates
from tracetally.analysis.checks.cache_opportunity import REQUIRES as CACHE_REQUIRES
from tracetally.analysis.checks.cache_opportunity import cache_opportunity
from tracetally.analysis.checks.model_swap import REQUIRES as SWAP_REQUIRES
from tracetally.analysis.checks.model_swap import model_swap
from tracetally.analysis.checks.observability import REQUIRES as GAP_REQUIRES
from tracetally.analysis.checks.observability import observability_gap
from tracetally.analysis.checks.reliability import (
    ERROR_REQUIRES,
    LATENCY_REQUIRES,
    error_rates,
    latency_tail,
)
from tracetally.analysis.checks.retry_waste import REQUIRES as RETRY_REQUIRES
from tracetally.analysis.checks.retry_waste import retry_waste
from tracetally.analysis.checks.token_bloat import REQUIRES as BLOAT_REQUIRES
from tracetally.analysis.checks.token_bloat import token_bloat
from tracetally.config import AnalyzeConfig
from tracetally.core.schema import Capability, Finding
from tracetally.core.store import TraceStore
from tracetally.pricing.table import PriceTable

CheckFn = Callable[[TraceStore, PriceTable, AnalyzeConfig], list[Finding]]


@dataclass(frozen=True)
class Check:
    id: str
    requires: frozenset[Capability]
    run: CheckFn


CHECKS: tuple[Check, ...] = (
    Check("token_bloat", BLOAT_REQUIRES, token_bloat),
    Check("cache_opportunity", CACHE_REQUIRES, cache_opportunity),
    Check("batch_candidates", BATCH_REQUIRES, batch_candidates),
    Check("retry_waste", RETRY_REQUIRES, retry_waste),
    Check("agent_loops", AGENT_REQUIRES, agent_loops),
    Check("model_swap", SWAP_REQUIRES, model_swap),
    Check("observability_gap", GAP_REQUIRES, observability_gap),
    Check("latency_tail", LATENCY_REQUIRES, latency_tail),
    Check("error_rates", ERROR_REQUIRES, error_rates),
)

CHECK_IDS = frozenset(check.id for check in CHECKS)

__all__ = ["CHECK_IDS", "CHECKS", "Check"]
