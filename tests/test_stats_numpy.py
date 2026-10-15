# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from tracetally.core.stats import percentile_linear


@settings(max_examples=40, deadline=None)
@given(
    st.lists(
        st.floats(min_value=0, max_value=60_000, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=40,
    )
)
def test_percentiles_match_numpy_linear(values: list[float]) -> None:
    for percent in (50, 95, 99):
        got = percentile_linear(values, percent)
        expected = float(np.percentile(np.asarray(values, dtype=float), percent, method="linear"))
        assert abs(got - expected) <= 1.0
        assert got == pytest_approx(expected)


def pytest_approx(expected: float) -> object:
    import pytest

    return pytest.approx(expected, rel=1e-9, abs=1e-6)
