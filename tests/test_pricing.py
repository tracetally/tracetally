# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from tracetally.pricing.catalog import BUNDLED_SNAPSHOT_DATE
from tracetally.pricing.fetch import LITELLM_PRICES_URL
from tracetally.pricing.table import load_bundled


def test_bundled_snapshot_prices_the_listed_models() -> None:
    table = load_bundled()
    assert table.provenance.source == "bundled"
    assert table.provenance.url == LITELLM_PRICES_URL
    assert table.provenance.retrieved_on == BUNDLED_SNAPSHOT_DATE
    assert len(table.provenance.sha256) == 64
    mini = table.resolve("openai", "gpt-4o-mini")
    assert mini is not None
    assert mini.input == Decimal("0.15")
    assert mini.cached_input == Decimal("0.075")
    assert mini.output == Decimal("0.60")
    assert mini.match == "exact"
    sonnet = table.resolve("anthropic", "claude-sonnet-4-5")
    assert sonnet is not None and sonnet.input == Decimal("3") and sonnet.output == Decimal("15")
    flash = table.resolve("gemini", "gemini-2.5-flash")
    assert flash is not None and flash.input == Decimal("0.30") and flash.output == Decimal("2.50")
    gpt4o = table.resolve("openai", "gpt-4o")
    assert gpt4o is not None and gpt4o.input == Decimal("2.50") and gpt4o.output == Decimal("10")
    assert table.resolve("unknown", "gpt-4o-mini") is None
    table.assert_fresh(table.retrieved_on, max_age_days=60)
    try:
        table.assert_fresh(table.retrieved_on + timedelta(days=61), max_age_days=60)
    except Exception as exc:
        assert "60" in str(exc)
    else:
        raise AssertionError("stale table was accepted")


def test_package_json_is_only_the_litellm_snapshot() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    found = sorted(path.relative_to(root).as_posix() for path in root.rglob("*.json"))
    assert found == ["tracetally/pricing/data/model_prices_and_context_window.json"]
