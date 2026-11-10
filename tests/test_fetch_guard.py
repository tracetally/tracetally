# SPDX-License-Identifier: Apache-2.0
"""A swallowed price fetch still fails the test unless the test opts in."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

ROOT = Path(__file__).resolve().parents[1]
_CONFTEST = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")


def test_stray_price_fetch_fails_at_teardown(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(_CONFTEST)
    pytester.makepyfile(
        """
        from tracetally.pricing.table import load_prices

        def test_fetches(tmp_path):
            table = load_prices(offline=False, cache_path=tmp_path)
            assert table.provenance.source == "bundled"
        """
    )
    result = pytester.runpytest("--disable-socket", "-p", "no:cacheprovider")
    # The test body passes because acquire_prices swallows the stub's
    # AssertionError. Teardown is what fails the run.
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*live price fetch was attempted*"])


def test_opt_in_allows_the_swallowed_fallback(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(_CONFTEST)
    pytester.makepyfile(
        """
        from tracetally.pricing.table import load_prices

        def test_fetches(allow_price_fetch, tmp_path):
            table = load_prices(offline=False, cache_path=tmp_path)
            assert table.provenance.source == "bundled"
        """
    )
    result = pytester.runpytest("--disable-socket", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
