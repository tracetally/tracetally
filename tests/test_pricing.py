# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tracetally.core.errors import PriceError
from tracetally.pricing.table import load_bundled, load_prices, parse_price_text

OPENAI = "https://developers.openai.com/api/docs/pricing"
ANTHROPIC = "https://platform.claude.com/docs/en/about-claude/pricing"
GEMINI = "https://ai.google.dev/gemini-api/docs/pricing"


def test_bundled_table_is_sourced_and_covers_three_providers() -> None:
    table = load_bundled()
    assert table.table_id == "2026-10"
    assert table.retrieved_on == date(2026, 10, 15)
    assert set(table.providers) == {"openai", "anthropic", "gemini"}
    assert len(table.iter_models()) >= 25
    for price in table.iter_models():
        assert price.source_url.startswith("https://")
        assert price.retrieved_on == date(2026, 10, 15)
        assert price.input >= 0
        assert price.output >= 0
    for name, bucket in table.providers.items():
        assert bucket.batch_discount == Decimal("0.5")
        assert bucket.batch_source_url
        assert bucket.batch_retrieved_on == date(2026, 10, 15)
        assert name


def test_known_standard_rates() -> None:
    table = load_bundled()
    mini = table.resolve("openai", "gpt-4o-mini")
    assert mini is not None
    assert mini.input == Decimal("0.15")
    assert mini.cached_input == Decimal("0.075")
    assert mini.output == Decimal("0.60")
    assert mini.source_url == OPENAI
    snapshot = table.resolve("OpenAI", "gpt-4o-2024-05-13")
    assert snapshot is not None
    assert snapshot.input == Decimal("5.00")
    assert snapshot.output == Decimal("15")
    sonnet = table.resolve("anthropic", "claude-sonnet-4-5")
    assert sonnet is not None and sonnet.input == Decimal("3")
    assert sonnet.source_url == ANTHROPIC
    flash = table.resolve("google", "gemini-2.5-flash")
    assert flash is not None and flash.input == Decimal("0.30")
    assert flash.source_url == GEMINI
    assert table.resolve("gcp.gemini", "gemini-2.5-pro") is not None


def test_resolution_is_exact_alias_or_date_suffix_and_never_a_guess() -> None:
    table = load_bundled()
    alias = table.resolve("openai", "gpt-4o-mini-2024-07-18")
    assert alias is not None and alias.model == "gpt-4o-mini" and alias.match == "alias"
    dated = table.resolve("openai", "gpt-4o-2024-08-06")
    assert dated is not None and dated.model == "gpt-4o" and dated.match == "date_suffix"
    assert dated.input == Decimal("2.50")
    may = table.resolve("openai", "gpt-4o-2024-05-13")
    assert may is not None and may.match == "exact" and may.input == Decimal("5.00")
    assert table.resolve("openai", "gpt-4o-mini-ultra") is None
    assert table.resolve("openai", "gpt-5-mini-special") is None
    assert table.resolve("openai", "GPT-4o-mini") is None
    turbo_1106 = table.resolve("openai", "gpt-3.5-turbo-1106")
    assert turbo_1106 is not None and turbo_1106.input == Decimal("1")
    assert table.resolve("vertex_ai", "gemini-2.5-flash") is None
    assert table.resolve("azure.ai.openai", "gpt-4o-mini") is None
    assert table.resolve("unknown", "gpt-4o-mini") is None


def test_cost_math_cache_batch_and_long_context() -> None:
    table = load_bundled()
    mini = table.resolve("openai", "gpt-4o-mini")
    assert mini is not None
    cost, applied = mini.cost(input_tokens=1000, cached_input_tokens=200, output_tokens=100)
    assert cost == Decimal("0.000195")
    assert applied is False
    batched, applied = mini.cost(
        input_tokens=1000, cached_input_tokens=200, output_tokens=100, is_batch=True
    )
    assert batched == Decimal("0.0000975")
    assert applied is True
    no_cache = table.resolve("openai", "gpt-4o-2024-05-13")
    assert no_cache is not None
    plain, _ = no_cache.cost(input_tokens=1_000_000, cached_input_tokens=100, output_tokens=0)
    assert plain == Decimal("5")
    pro = table.resolve("gemini", "gemini-2.5-pro")
    assert pro is not None
    short, _ = pro.cost(input_tokens=200_000, output_tokens=0)
    long, _ = pro.cost(input_tokens=200_001, output_tokens=0)
    assert short == Decimal("200000") * Decimal("1.25") / Decimal(1_000_000)
    assert long == Decimal("200001") * Decimal("2.50") / Decimal(1_000_000)
    haiku = table.resolve("anthropic", "claude-haiku-5-5")
    assert haiku is not None
    base, _ = haiku.cost(input_tokens=100_000, output_tokens=0)
    over, _ = haiku.cost(input_tokens=100_001, output_tokens=0)
    assert base == Decimal("0.01")
    assert over == Decimal("100001") * Decimal("0.50") / Decimal(1_000_000)


def test_override_replaces_and_invalid_files_name_the_field(tmp_path: Path) -> None:
    override = tmp_path / "prices.yaml"
    override.write_text(
        """
schema_version: 1
table_id: "negotiated"
currency: USD
unit: per_1m_tokens
retrieved_on: "2026-10-15"
providers:
  openai:
    models:
      gpt-4o-mini:
        input: "0.01"
        output: "0.02"
        source_url: "https://example.com/contract"
        retrieved_on: "2026-10-15"
      acme-private-llm:
        input: "1"
        output: "1"
        source_url: "https://example.com/acme"
        retrieved_on: "2026-10-15"
""",
        encoding="utf-8",
    )
    table = load_prices(override)
    assert table.display_id == "2026-10+negotiated"
    mini = table.resolve("openai", "gpt-4o-mini")
    assert mini is not None and mini.input == Decimal("0.01")
    private = table.resolve("openai", "acme-private-llm")
    assert private is not None
    still = table.resolve("anthropic", "claude-sonnet-4-5")
    assert still is not None and still.input == Decimal("3")

    broken = tmp_path / "broken.yaml"
    broken.write_text(
        """
schema_version: 1
table_id: "x"
currency: USD
unit: per_1m_tokens
retrieved_on: "2026-10-15"
providers:
  openai:
    models:
      gpt-4o:
        output: "1"
        source_url: "https://example.com"
        retrieved_on: "2026-10-15"
""",
        encoding="utf-8",
    )
    with pytest.raises(PriceError, match="input"):
        load_prices(broken)


def test_freshness_check_is_injected_not_a_wall_clock_failure() -> None:
    table = load_bundled()
    table.assert_fresh(date(2026, 10, 15), max_age_days=60)
    with pytest.raises(PriceError, match="60"):
        table.assert_fresh(date(2026, 12, 20), max_age_days=60)


def test_price_file_rejects_malformed_documents(tmp_path: Path) -> None:
    with pytest.raises(PriceError, match="invalid YAML"):
        parse_price_text("providers: [", origin="bad.yaml")
    with pytest.raises(PriceError, match="mapping"):
        parse_price_text("- just\n- a\n- list\n", origin="list.yaml")
    with pytest.raises(PriceError, match="schema_version"):
        parse_price_text("schema_version: 2\n", origin="old.yaml")
    with pytest.raises(PriceError, match="currency"):
        parse_price_text(
            "schema_version: 1\ntable_id: x\ncurrency: EUR\nunit: per_1m_tokens\n"
            "retrieved_on: '2026-10-15'\nproviders: {openai: {models: {m: {}}}}\n",
            origin="eur.yaml",
        )
    missing = tmp_path / "nope.yaml"
    with pytest.raises(PriceError, match="not found"):
        load_prices(missing)
    binary = tmp_path / "bin.yaml"
    binary.write_bytes(b"\xff\xfe")
    with pytest.raises(PriceError, match="UTF-8"):
        load_prices(binary)
    dated = parse_price_text(
        """
schema_version: 1
table_id: "d"
currency: USD
unit: per_1m_tokens
retrieved_on: 2026-10-15
notes: keep
providers:
  custom:
    models:
      m:
        input: "1"
        cached_input: "0.5"
        output: "2"
        source_url: "http://example.com/p"
        retrieved_on: "2026-10-15"
        aliases: ["m-latest"]
        caveat: "text only"
""",
        origin="dated.yaml",
    )
    assert dated.providers["custom"].models["m"].cached_input == Decimal("0.5")
    assert dated.resolve("custom", "m-2026-10-15") is not None
    with pytest.raises(PriceError, match="aliases"):
        parse_price_text(
            """
schema_version: 1
table_id: "a"
currency: USD
unit: per_1m_tokens
retrieved_on: "2026-10-15"
providers:
  openai:
    models:
      m:
        input: "1"
        output: "1"
        source_url: "https://example.com"
        retrieved_on: "2026-10-15"
        aliases: [1]
""",
            origin="alias.yaml",
        )
    with pytest.raises(PriceError, match="long-context"):
        parse_price_text(
            """
schema_version: 1
table_id: "l"
currency: USD
unit: per_1m_tokens
retrieved_on: "2026-10-15"
providers:
  openai:
    models:
      m:
        input: "1"
        output: "1"
        source_url: "https://example.com"
        retrieved_on: "2026-10-15"
        long_input: "2"
""",
            origin="long.yaml",
        )


def test_price_file_rejects_a_guessable_shape() -> None:
    with pytest.raises(PriceError, match="source_url"):
        parse_price_text(
            """
schema_version: 1
table_id: "x"
currency: USD
unit: per_1m_tokens
retrieved_on: "2026-10-15"
providers:
  openai:
    batch_discount:
      value: "0.5"
    models:
      gpt-4o:
        input: "1"
        output: "1"
        source_url: "https://example.com"
        retrieved_on: "2026-10-15"
""",
            origin="test",
        )
