# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture(autouse=True)
def _isolated_price_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep price-cache lookups off the developer machine and out of other tests."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))


@pytest.fixture(autouse=True)
def _no_live_price_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches the live price fetch. fetch.py tests call it directly."""
    import tracetally.pricing.source as price_source

    def blocked(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("live price fetch is disabled in tests")

    monkeypatch.setattr(price_source, "fetch_prices", blocked)
