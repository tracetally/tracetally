# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import date, timedelta
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
    assert table.retrieved_on == date(2026, 10, 22)
    assert set(table.providers) == {"openai", "anthropic", "gemini"}
    assert len(table.iter_models()) >= 40
    for price in table.iter_models():
        assert price.source_url.startswith("https://")
        assert price.retrieved_on == date(2026, 10, 22)
        assert price.input >= 0
        assert price.output >= 0
    for name, bucket in table.providers.items():
        assert bucket.batch_discount == Decimal("0.5")
        assert bucket.batch_source_url
        assert bucket.batch_retrieved_on == date(2026, 10, 22)
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
    gpt5 = table.resolve("openai", "gpt-5")
    assert gpt5 is not None and gpt5.reasoning == gpt5.output == Decimal("10")
    plain, _ = gpt5.cost(input_tokens=0, output_tokens=1_000_000)
    assert plain == Decimal("10")
    assert table.resolve("openai", "gpt-4o").reasoning is None
    assert table.resolve("anthropic", "claude-sonnet-4-5").reasoning is None
    flash_price = table.resolve("gemini", "gemini-2.5-flash")
    assert flash_price is not None and flash_price.reasoning == Decimal("2.50")
    preview = table.resolve("gemini", "gemini-3.1-pro-preview")
    assert preview is not None and preview.long_output == Decimal("18")
    hosts = (
        "https://developers.openai.com/api/docs/pricing",
        "https://platform.claude.com/docs/en/about-claude/pricing",
        "https://ai.google.dev/gemini-api/docs/pricing",
    )
    for price in table.iter_models():
        assert price.source_url in hosts


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
    assert table.resolve("openai", "gpt-3.5-turbo-1106") is None
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
    # gpt-4o-mini publishes no batch cached rate. Cached tokens stay at $0.075.
    # Uncached input and output take the 50% batch multiplier.
    # 800 * 0.075 + 200 * 0.075 + 100 * 0.30, per 1M tokens.
    assert batched == Decimal("0.000105")
    assert applied is True
    assert mini.batch_cached == "standard"
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


def test_batch_discount_follows_the_published_cached_rule() -> None:
    """Batch is not a 50% multiplier on every token.

    Numbers are the published per-1M rates, checked against the provider pages
    on 2026-10-22. A fully cached call makes the rule visible.
    """
    table = load_bundled()
    flash = table.resolve("gemini", "gemini-2.5-flash")
    assert flash is not None and flash.batch_cached == "standard"
    flash_batch, applied = flash.cost(
        input_tokens=1_000_000, cached_input_tokens=1_000_000, output_tokens=0, is_batch=True
    )
    assert applied is True
    assert flash_batch == Decimal("0.03")

    gpt41 = table.resolve("openai", "gpt-4.1")
    assert gpt41 is not None and gpt41.batch_cached == "standard"
    gpt41_batch, _ = gpt41.cost(
        input_tokens=1_000_000, cached_input_tokens=400_000, output_tokens=0, is_batch=True
    )
    # 600k uncached at the $1 batch input rate, 400k cached left at $0.50.
    assert gpt41_batch == Decimal("0.8")

    gpt5 = table.resolve("openai", "gpt-5")
    assert gpt5 is not None and gpt5.batch_cached == "stack"
    gpt5_batch, _ = gpt5.cost(
        input_tokens=1_000_000, cached_input_tokens=1_000_000, output_tokens=0, is_batch=True
    )
    assert gpt5_batch == Decimal("0.0625")

    sonnet = table.resolve("anthropic", "claude-sonnet-4-5")
    assert sonnet is not None and sonnet.batch_cached == "stack"
    sonnet_batch, _ = sonnet.cost(
        input_tokens=1_000_000, cached_input_tokens=1_000_000, output_tokens=0, is_batch=True
    )
    assert sonnet_batch == Decimal("0.15")

    # Models added with the 2026-10-22 table. Stack only where the batch page
    # publishes a cached rate at half the standard cached rate.
    stacked = {
        "gpt-5.1": Decimal("0.0625"),
        "gpt-5.2": Decimal("0.0875"),
        "gpt-5.4-mini": Decimal("0.0375"),
        "gpt-5.4-nano": Decimal("0.01"),
    }
    for model, fully_cached_batch in stacked.items():
        price = table.resolve("openai", model)
        assert price is not None and price.batch_cached == "stack"
        amount, _ = price.cost(
            input_tokens=1_000_000, cached_input_tokens=1_000_000, output_tokens=0, is_batch=True
        )
        assert amount == fully_cached_batch
    pro = table.resolve("openai", "gpt-5-pro")
    assert pro is not None and pro.batch_cached == "standard" and pro.cached_input is None
    pro_batch, _ = pro.cost(
        input_tokens=1_000_000, cached_input_tokens=1_000_000, output_tokens=0, is_batch=True
    )
    assert pro_batch == Decimal("15")
    flash3 = table.resolve("gemini", "gemini-3-flash-preview")
    assert flash3 is not None and flash3.batch_cached == "standard"
    flash3_batch, _ = flash3.cost(
        input_tokens=1_000_000, cached_input_tokens=1_000_000, output_tokens=0, is_batch=True
    )
    assert flash3_batch == Decimal("0.05")
    lite = table.resolve("gemini", "gemini-3.1-flash-lite")
    assert lite is not None and lite.batch_cached == "stack"
    lite_batch, _ = lite.cost(
        input_tokens=1_000_000, cached_input_tokens=1_000_000, output_tokens=0, is_batch=True
    )
    assert lite_batch == Decimal("0.0125")
    flash35 = table.resolve("gemini", "gemini-3.5-flash-lite")
    assert flash35 is not None and flash35.batch_cached == "standard"
    flash35_batch, _ = flash35.cost(
        input_tokens=1_000_000, cached_input_tokens=1_000_000, output_tokens=0, is_batch=True
    )
    assert flash35_batch == Decimal("0.03")


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
    table.assert_fresh(table.retrieved_on, max_age_days=60)
    with pytest.raises(PriceError, match="60"):
        table.assert_fresh(table.retrieved_on + timedelta(days=61), max_age_days=60)


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


def test_merge_keeps_an_explicit_model_batch_cached_mode(tmp_path: Path) -> None:
    override = tmp_path / "prices.yaml"
    override.write_text(
        """
schema_version: 1
table_id: "extra"
currency: USD
unit: per_1m_tokens
retrieved_on: "2026-10-15"
providers:
  openai:
    models:
      acme-private-llm:
        input: "1"
        output: "1"
        source_url: "https://example.com/acme"
        retrieved_on: "2026-10-15"
""",
        encoding="utf-8",
    )
    table = load_prices(override)
    gpt5 = table.resolve("openai", "gpt-5")
    assert gpt5 is not None and gpt5.batch_cached == "stack"
    mini = table.resolve("openai", "gpt-4o-mini")
    assert mini is not None and mini.batch_cached == "standard"
    with pytest.raises(PriceError, match="batch_cached"):
        parse_price_text(
            """
schema_version: 1
table_id: "bad-mode"
currency: USD
unit: per_1m_tokens
retrieved_on: "2026-10-22"
providers:
  openai:
    batch_cached:
      mode: half
      source_url: "https://example.com/batch"
      retrieved_on: "2026-10-22"
    models:
      m:
        input: "1"
        output: "1"
        source_url: "https://example.com"
        retrieved_on: "2026-10-22"
""",
            origin="mode.yaml",
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
