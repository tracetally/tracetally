# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture
def examples() -> Path:
    return ROOT / "examples"


@pytest.fixture(autouse=True)
def _isolated_price_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep price-cache lookups off the developer machine and out of other tests."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))


@pytest.fixture
def allow_price_fetch() -> None:
    """Opt in when a test intentionally hits the swallowed live-price fallback.

    ``acquire_prices`` catches ``Exception``, so the autouse stub cannot fail
    the test by raising. Request this fixture to allow that fallback.
    """
    return None


@pytest.fixture(autouse=True)
def _no_live_price_fetch(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[None]:
    """Record a live price fetch and fail the test afterwards.

    ``src/tracetally/pricing/source.py`` catches ``Exception`` around
    ``fetch_prices`` and falls back to the cache or the bundle, so an
    ``AssertionError`` raised here never fails the test. The call is recorded
    and teardown fails unless the test requests ``allow_price_fetch``.
    Tests that replace ``fetch_prices`` themselves do not hit this stub.
    """
    import tracetally.pricing.source as price_source

    calls: list[object] = []

    def blocked(*args: object, **kwargs: object) -> bytes:
        calls.append((args, kwargs))
        raise AssertionError("live price fetch is disabled in tests")

    monkeypatch.setattr(price_source, "fetch_prices", blocked)
    yield
    if calls and "allow_price_fetch" not in request.fixturenames:
        pytest.fail("live price fetch was attempted; use --offline or request allow_price_fetch")
