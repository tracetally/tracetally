# SPDX-License-Identifier: Apache-2.0
"""Percentiles and rates.

Percentiles use the same linear interpolation as ``numpy.percentile(..., method="linear")``:
the rank is ``p/100 * (n - 1)`` and the result is interpolated between the surrounding
order statistics. Numpy is a test-only dependency.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

# Latency percentiles are hidden below this sample size so a handful of calls
# cannot look like a distribution.
MIN_PERCENTILE_N = 20


def percentile_linear(values: Sequence[float], percent: float) -> float:
    """Linear percentile in ``[0, 100]``.

    Raises ``ValueError`` on an empty sample or a percent outside 0–100.
    """
    if not values:
        raise ValueError("percentile of an empty sample")
    if percent < 0 or percent > 100:
        raise ValueError("percent must be between 0 and 100")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1 or percent == 0:
        return ordered[0]
    if percent == 100:
        return ordered[-1]
    rank = (percent / 100.0) * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def percentiles_or_none(
    values: Sequence[float],
    percents: Sequence[float] = (50, 95, 99),
    *,
    min_n: int = MIN_PERCENTILE_N,
) -> dict[float, float] | None:
    """Return percentiles, or ``None`` when the sample is smaller than ``min_n``."""
    if len(values) < min_n:
        return None
    return {p: percentile_linear(values, p) for p in percents}


def rate(numerator: int, denominator: int) -> float | None:
    """``numerator / denominator``, or ``None`` when there is nothing to divide."""
    if denominator <= 0:
        return None
    if numerator < 0:
        raise ValueError("numerator must be >= 0")
    return numerator / denominator
