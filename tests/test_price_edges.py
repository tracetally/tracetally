# SPDX-License-Identifier: Apache-2.0
"""Cost-engine edge cases. Inputs are built in tmp_path; nothing here is a data file."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from tracetally.analysis.summary import summarize
from tracetally.config import ReaderConfig
from tracetally.pricing.config import load_pricing_config
from tracetally.pricing.table import table_from_bytes
from tracetally.readers.base import load_paths

_MILLION = Decimal(1_000_000)


def _per_token(per_million: str) -> float:
    return float(Decimal(per_million) / _MILLION)


def _price_file(tmp_path: Path, models: dict[str, object]) -> Path:
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(models), encoding="utf-8")
    return path


def _model(
    provider: str,
    incoming: str,
    outgoing: str,
    **extra: object,
) -> dict[str, object]:
    row: dict[str, object] = {
        "litellm_provider": provider,
        "input_cost_per_token": _per_token(incoming),
        "output_cost_per_token": _per_token(outgoing),
        "mode": "chat",
    }
    row.update(extra)
    return row


def _jsonl(tmp_path: Path, rows: list[dict[str, object]], name: str = "calls.jsonl") -> Path:
    path = tmp_path / name
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _call(model: str, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "model": model,
        "provider": "openai",
        "endpoint": "app",
        "start_time": "2026-10-01T00:00:00Z",
        "input_tokens": 1_000_000,
        "output_tokens": 0,
    }
    row.update(extra)
    return row


def _summarize(path: Path, price_path: Path, *, config: Path | None = None):
    pricing = load_pricing_config(config) if config is not None else None
    table = table_from_bytes(price_path.read_bytes(), config=pricing)
    return summarize(load_paths([path], ReaderConfig()), table)


def test_ollama_zero_price_is_cost_unknown(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {
            "ollama/llama3": _model("ollama", "0", "0"),
            "promo-zero": _model("openai", "0", "0"),
            "gpt-4o-mini": _model("openai", "2", "0"),
        },
    )
    path = _jsonl(
        tmp_path,
        [
            _call("llama3", provider="ollama", output_tokens=50, status="error", http_status=500),
            _call("promo-zero", input_tokens=500, output_tokens=20),
            _call("gpt-4o-mini", input_tokens=1_000_000, output_tokens=0),
        ],
    )
    result = _summarize(path, prices)
    by_model = {(row.provider, row.model): row for row in result.by_model}
    assert by_model[("ollama", "llama3")].cost_usd is None
    assert by_model[("openai", "promo-zero")].cost_usd is None
    assert by_model[("openai", "gpt-4o-mini")].cost_usd == Decimal("2")
    assert result.cost_usd == Decimal("2")
    assert result.n_error == 1
    text = " ".join(result.warnings)
    assert "cost unknown" in text
    assert "list price is zero" in text
    assert "0E" not in text


def test_priced_share_counts_only_priced_calls(tmp_path: Path) -> None:
    prices = _price_file(tmp_path, {"gpt-4o-mini": _model("openai", "1", "0")})
    path = _jsonl(
        tmp_path,
        [
            _call("gpt-4o-mini", input_tokens=1_000),
            _call("llama3", provider="ollama", input_tokens=1_000),
        ],
    )
    result = _summarize(path, prices)
    assert result.priced_calls == 1
    assert result.unpriced_calls == 1
    assert result.priced_share == "50.00%"
    assert "priced share" in result.to_text() or "50.00%" in result.to_text()


def test_provider_prefix_and_litellm_alias_of(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {
            "gpt-4o-mini": _model("openai", "1", "0"),
            "gpt-4o-mini-latest": {
                "alias_of": "gpt-4o-mini",
                "litellm_provider": "openai",
            },
            "gemini-2.5-flash": _model("vertex_ai", "9", "0"),
            "gemini/gemini-2.5-flash": _model("gemini", "1.25", "0"),
        },
    )
    alias = _jsonl(tmp_path, [_call("gpt-4o-mini-latest")], name="alias.jsonl")
    alias_result = _summarize(alias, prices)
    assert alias_result.cost_usd == Decimal("1")
    assert alias_result.by_model[0].price_match == "alias_of"

    prefixed = _jsonl(
        tmp_path,
        [_call("gemini-2.5-flash", provider="gemini")],
        name="prefix.jsonl",
    )
    prefixed_result = _summarize(prefixed, prices)
    assert prefixed_result.cost_usd == Decimal("1.25")
    assert prefixed_result.by_model[0].price_match == "provider_prefix"


def test_date_suffix_match_is_labelled_approx(tmp_path: Path) -> None:
    prices = _price_file(tmp_path, {"gpt-4o": _model("openai", "2.5", "10")})
    path = _jsonl(tmp_path, [_call("gpt-4o-2099-01-01", input_tokens=1_000_000)])
    result = _summarize(path, prices)
    row = result.by_model[0]
    assert row.cost_usd == Decimal("2.5")
    assert row.price_match == "date_suffix"
    assert row.price_label == "approx."


def test_fine_tune_listed_price_else_base_estimate(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {
            "gpt-4o-mini": _model("openai", "1", "0"),
            "ft:gpt-4o-mini": _model("openai", "5", "0"),
        },
    )
    listed = _jsonl(
        tmp_path,
        [_call("ft:gpt-4o-mini:acme:name:abc")],
        name="listed.jsonl",
    )
    listed_result = _summarize(listed, prices)
    assert listed_result.cost_usd == Decimal("5")
    assert listed_result.by_model[0].price_label is None
    assert listed_result.by_model[0].price_match == "fine_tune"

    estimate = _jsonl(
        tmp_path,
        [_call("ft:gpt-4o-mini-2099-01-01:acme:name:abc")],
        name="estimate.jsonl",
    )
    estimate_result = _summarize(estimate, prices)
    assert estimate_result.cost_usd == Decimal("1")
    assert estimate_result.by_model[0].price_label == "estimate"
    assert estimate_result.by_model[0].price_match == "fine_tune"


def test_bedrock_region_prefix_uses_billed_provider(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {
            "claude-x": _model("anthropic", "3", "0"),
            "anthropic.claude-x": _model("bedrock", "8", "0"),
        },
    )
    path = _jsonl(
        tmp_path,
        [_call("us.anthropic.claude-x", provider="bedrock", input_tokens=1_000_000)],
    )
    result = _summarize(path, prices)
    assert result.cost_usd == Decimal("8")
    assert result.by_model[0].price_match == "region_prefix"
    assert result.by_model[0].price_label == "approx."


def test_vertex_at_date_uses_billed_provider(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {
            "gemini-2.5-pro": _model("vertex_ai", "9", "0"),
            "gemini/gemini-2.5-pro": _model("gemini", "1.25", "0"),
        },
    )
    path = _jsonl(
        tmp_path,
        [_call("gemini-2.5-pro@20250121", provider="vertex_ai", input_tokens=1_000_000)],
    )
    result = _summarize(path, prices)
    assert result.cost_usd == Decimal("9")
    assert result.by_model[0].price_match == "date_suffix"
    assert result.by_model[0].price_label == "approx."


def test_openai_cached_tokens_are_a_subset_of_input(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {
            "gpt-4o": _model(
                "openai",
                "2",
                "4",
                cache_read_input_token_cost=_per_token("0.5"),
            )
        },
    )
    path = _jsonl(
        tmp_path,
        [_call("gpt-4o", input_tokens=1000, cached_input_tokens=400, output_tokens=100)],
    )
    result = _summarize(path, prices)
    store = load_paths([path], ReaderConfig())
    # The export count is left intact. 600 uncached at $2, 400 cached at $0.50,
    # 100 output at $4, per 1M tokens. The cache read is subtracted once.
    assert int(store.input_tokens[0]) == 1000
    assert store.cache_accounting[0] == "included"
    assert result.cost_usd == Decimal("0.0018")


def test_anthropic_cache_read_and_write_are_separate(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {
            "claude-sonnet": _model(
                "anthropic",
                "3",
                "15",
                cache_read_input_token_cost=_per_token("0.30"),
                cache_creation_input_token_cost=_per_token("3.75"),
            )
        },
    )
    path = _jsonl(
        tmp_path,
        [
            _call(
                "claude-sonnet",
                provider="anthropic",
                input_tokens=1000,
                cached_input_tokens=400,
                cache_creation_input_tokens=100,
                output_tokens=0,
            )
        ],
    )
    store = load_paths([path], ReaderConfig())
    assert int(store.input_tokens[0]) == 1000
    assert int(store.cached_input_tokens[0]) == 400
    assert int(store.cache_creation_tokens[0]) == 100
    assert store.cache_accounting[0] == "separate"
    result = _summarize(path, prices)
    # 1000 at $3, 400 reads at $0.30, 100 writes at $3.75, per 1M. Not subtracted.
    assert result.cost_usd == Decimal("0.003495")

    observed = tmp_path / "anthropic.json"
    observed.write_text(
        json.dumps(
            {
                "id": "g1",
                "trace_id": "t1",
                "type": "GENERATION",
                "start_time": "2026-10-01T00:00:00Z",
                "model": "claude-sonnet",
                "metadata": {"provider": "anthropic"},
                "usage_details": {
                    "input": 1000,
                    "output": 0,
                    "cache_read_input_tokens": 400,
                    "cache_creation_input_tokens": 100,
                },
            }
        ),
        encoding="utf-8",
    )
    langfuse = load_paths([observed], ReaderConfig(format_name="langfuse"))
    assert int(langfuse.input_tokens[0]) == 1000
    assert langfuse.cache_accounting[0] == "separate"
    assert summarize(langfuse, table_from_bytes(prices.read_bytes())).cost_usd == Decimal(
        "0.003495"
    )


def test_absent_cache_fields_are_not_invented(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {
            "gpt-4o": _model(
                "openai",
                "2",
                "4",
                cache_read_input_token_cost=_per_token("0.5"),
            )
        },
    )
    path = _jsonl(tmp_path, [_call("gpt-4o", input_tokens=1000, output_tokens=0)])
    store = load_paths([path], ReaderConfig())
    assert store.cache_accounting[0] == "absent"
    assert int(store.cached_input_tokens[0]) == 0
    result = _summarize(path, prices)
    # The whole input is priced at $2 per 1M. The published cache rate is not applied.
    assert result.cost_usd == Decimal("0.002")


def test_over_200k_tier_is_per_request(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {
            "long-model": _model(
                "openai",
                "1",
                "0",
                input_cost_per_token_above_200k_tokens=_per_token("3"),
                output_cost_per_token_above_200k_tokens=_per_token("0"),
            )
        },
    )
    path = _jsonl(
        tmp_path,
        [
            _call("long-model", input_tokens=150_000, record_id="a"),
            _call("long-model", input_tokens=150_000, record_id="b"),
            _call("long-model", input_tokens=250_000, record_id="c"),
        ],
    )
    result = _summarize(path, prices)
    # 150k and 150k stay on the $1 rate. Only the 250k request uses $3.
    assert result.cost_usd == Decimal("1.05")
    assert result.by_model[0].price_label is None


@pytest.mark.parametrize(
    ("provider", "model", "separate"),
    [
        ("openai", "gpt-4o", False),
        ("gemini", "gemini-2.5-flash", False),
        ("anthropic", "claude-sonnet", True),
        ("bedrock", "anthropic.claude-sonnet", True),
        ("vertex_ai", "claude-sonnet", True),
    ],
)
def test_cache_accounting_follows_the_provider(
    tmp_path: Path, provider: str, model: str, separate: bool
) -> None:
    prices = _price_file(
        tmp_path,
        {
            model: _model(
                provider,
                "1",
                "0",
                cache_read_input_token_cost=_per_token("0.25"),
                cache_creation_input_token_cost=_per_token("3.75"),
            )
        },
    )
    extra: dict[str, object] = {
        "provider": provider,
        "input_tokens": 1000,
        "cached_input_tokens": 400,
        "output_tokens": 0,
    }
    if separate:
        extra["cache_creation_input_tokens"] = 100
    path = _jsonl(tmp_path, [_call(model, **extra)])
    store = load_paths([path], ReaderConfig())
    result = _summarize(path, prices)
    if separate:
        assert store.cache_accounting[0] == "separate"
        # 1000 at $1, 400 reads at $0.25, 100 writes at $3.75. Not subtracted.
        assert result.cost_usd == Decimal("0.001475")
    else:
        assert store.cache_accounting[0] == "included"
        # 600 at $1 and 400 at $0.25. Subtracted once.
        assert result.cost_usd == Decimal("0.0007")


def test_non_200k_long_context_threshold_is_labelled_approx(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {
            "gem-128": _model(
                "gemini",
                "1",
                "0",
                input_cost_per_token_above_128k_tokens=_per_token("5"),
                output_cost_per_token_above_128k_tokens=_per_token("0"),
            ),
            "exact-200": _model(
                "openai",
                "1",
                "0",
                input_cost_per_token_above_200k_tokens=_per_token("3"),
                output_cost_per_token_above_200k_tokens=_per_token("0"),
            ),
        },
    )
    path = _jsonl(
        tmp_path,
        [
            _call("gem-128", provider="gemini", input_tokens=150_000, record_id="over"),
            _call("gem-128", provider="gemini", input_tokens=100_000, record_id="under"),
            _call("exact-200", input_tokens=250_000, record_id="long"),
        ],
    )
    result = _summarize(path, prices)
    by_model = {row.model: row for row in result.by_model}
    # 150k is past 128k, but only the 200k tier is applied exactly, so this stays
    # on the $1 base rate and is labelled approximate. 100k is under 128k.
    over = by_model["gem-128"]
    assert over.price_label == "approx."
    assert over.cost_usd == Decimal("0.25")
    exact = by_model["exact-200"]
    assert exact.price_label is None
    assert exact.cost_usd == Decimal("0.75")


def test_report_shows_billed_and_inferred_and_flags_a_large_gap(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from tracetally.analysis.engine import analyze
    from tracetally.report.render import ReportOptions, write_report

    prices = _price_file(tmp_path, {"gpt-4o": _model("openai", "1", "0")})
    path = _jsonl(
        tmp_path,
        [_call("gpt-4o", input_tokens=1_000_000, reported_cost_usd="2")],
    )
    table = table_from_bytes(prices.read_bytes())
    store = load_paths([path], ReaderConfig())
    result = summarize(store, table)
    assert result.cost_usd == Decimal("2")
    assert result.inferred_cost_usd == Decimal("1")
    assert result.billed_cost_usd == Decimal("2")
    assert result.cost_gap is True
    written = write_report(
        store,
        analyze(store, table),
        ReportOptions(
            out=tmp_path / "out",
            redact=False,
            include_content=False,
            content_requested=False,
            footer=False,
            generated_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
        ),
    )
    markdown = written.markdown.read_text(encoding="utf-8")
    html = written.html.read_text(encoding="utf-8")
    for document in (markdown, html):
        assert "2.000000" in document
        assert "1.000000" in document
        assert "large gap" in document
        assert "repriced from token counts" in document
    findings = json.loads(written.findings.read_text(encoding="utf-8"))
    assert findings["totals"]["billed_cost_usd"] == "2"
    assert findings["totals"]["inferred_cost_usd"] == "1"
    assert findings["totals"]["cost_gap"] is True


def test_discount_below_one_and_fx_above_zero(tmp_path: Path) -> None:
    from tracetally.core.errors import PriceError
    from tracetally.pricing.config import parse_pricing_toml

    with pytest.raises(PriceError, match=r"\[0, 1\)"):
        parse_pricing_toml('[discounts]\n"*" = "1"\n', origin="t.toml")
    with pytest.raises(PriceError, match=r"\[0, 1\)"):
        parse_pricing_toml('[discounts]\n"*" = "-0.1"\n', origin="t.toml")
    with pytest.raises(PriceError, match="greater than 0"):
        parse_pricing_toml('fx_rate = "0"\n', origin="t.toml")
    with pytest.raises(PriceError, match="greater than 0"):
        parse_pricing_toml('fx_rate = "-1"\n', origin="t.toml")

    prices = _price_file(tmp_path, {"gpt-4o": _model("openai", "2", "0")})
    config = tmp_path / "tracetally.toml"
    config.write_text('currency = "EUR"\nfx_rate = "0.5"\n', encoding="utf-8")
    path = _jsonl(tmp_path, [_call("gpt-4o")])
    result = _summarize(path, prices, config=config)
    assert result.currency == "EUR"
    assert result.fx_user_supplied is True
    assert "supplied by the user" in result.to_text()
    assert "EUR" in result.to_text()


def _otel_span(
    path: Path,
    *,
    provider: str,
    model: str,
    input_tokens: int,
    cached: int,
    created: int,
) -> None:
    def attr(key: str, value: object, kind: str) -> dict[str, object]:
        if kind == "int":
            return {"key": key, "value": {"intValue": str(value)}}
        return {"key": key, "value": {"stringValue": value}}

    document = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "t",
                                "spanId": "s",
                                "name": "chat",
                                "startTimeUnixNano": "1760000000000000000",
                                "endTimeUnixNano": "1760000000050000000",
                                "attributes": [
                                    attr("gen_ai.provider.name", provider, "str"),
                                    attr("gen_ai.response.model", model, "str"),
                                    attr("gen_ai.usage.input_tokens", input_tokens, "int"),
                                    attr("gen_ai.usage.output_tokens", 0, "int"),
                                    attr("gen_ai.usage.cache_read.input_tokens", cached, "int"),
                                    attr(
                                        "gen_ai.usage.cache_creation.input_tokens",
                                        created,
                                        "int",
                                    ),
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    path.write_text(json.dumps(document), encoding="utf-8")


def test_otel_anthropic_cache_is_inside_input_tokens(tmp_path: Path) -> None:
    from tracetally.pricing.table import load_bundled

    table = load_bundled()
    price = table.resolve("anthropic", "claude-sonnet-4-5")
    assert price is not None
    expected, _applied = price.cost(
        input_tokens=1000,
        output_tokens=0,
        cached_input_tokens=400,
        cache_creation_tokens=100,
        cache_accounting="separate",
    )
    # Quality's repro: 1500 OTel input tokens are 1000 fresh + 400 read + 100 write.
    assert expected == Decimal("0.003495")
    path = tmp_path / "otel.json"
    _otel_span(
        path,
        provider="anthropic",
        model="claude-sonnet-4-5",
        input_tokens=1500,
        cached=400,
        created=100,
    )
    store = load_paths([path], ReaderConfig())
    assert store.cache_accounting[0] == "embedded"
    assert int(store.input_tokens[0]) == 1500
    assert summarize(store, table).cost_usd == expected


def test_otel_bedrock_anthropic_cache_is_inside_input_tokens(tmp_path: Path) -> None:
    from tracetally.pricing.table import load_bundled

    table = load_bundled()
    model = "anthropic.claude-3-5-haiku-20241022-v1:0"
    price = table.resolve("aws.bedrock", model)
    assert price is not None
    expected, _applied = price.cost(
        input_tokens=1000,
        output_tokens=0,
        cached_input_tokens=400,
        cache_creation_tokens=100,
        cache_accounting="separate",
    )
    path = tmp_path / "bedrock.json"
    _otel_span(
        path,
        provider="aws.bedrock",
        model=model,
        input_tokens=1500,
        cached=400,
        created=100,
    )
    store = load_paths([path], ReaderConfig())
    assert store.cache_accounting[0] == "embedded"
    assert summarize(store, table).cost_usd == expected


def test_otel_provider_aliases_are_priced(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {
            "gemini-2.5-flash": _model("vertex_ai", "2", "0"),
            "gpt-4o": _model("azure", "3", "0"),
            "grok-2": _model("xai", "4", "0"),
        },
    )
    table = table_from_bytes(prices.read_bytes())
    cases = (
        ("gcp.vertex_ai", "gemini-2.5-flash", Decimal("2")),
        ("azure.ai.inference", "gpt-4o", Decimal("3")),
        ("x_ai", "grok-2", Decimal("4")),
    )
    for provider, model, amount in cases:
        path = tmp_path / f"{provider}.jsonl"
        path.write_text(
            json.dumps(_call(model, provider=provider, input_tokens=1_000_000)) + "\n",
            encoding="utf-8",
        )
        result = summarize(load_paths([path], ReaderConfig()), table)
        assert result.cost_usd == amount


def test_openai_compatible_providers_include_cached_tokens(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {"chat": _model("deepseek", "1", "0", cache_read_input_token_cost=_per_token("0.25"))},
    )
    path = _jsonl(
        tmp_path,
        [
            _call(
                "chat",
                provider="deepseek",
                input_tokens=1000,
                cached_input_tokens=400,
                output_tokens=0,
            )
        ],
    )
    store = load_paths([path], ReaderConfig())
    assert store.cache_accounting[0] == "included"
    assert _summarize(path, prices).cost_usd == Decimal("0.0007")


def test_otel_cache_overflow_is_counted_in_the_report(tmp_path: Path) -> None:
    from tracetally.analysis.engine import analyze
    from tracetally.pricing.table import load_bundled
    from tracetally.report.render import ReportOptions, write_report

    path = tmp_path / "overflow.json"
    _otel_span(
        path,
        provider="openai",
        model="gpt-4o-mini",
        input_tokens=100,
        cached=150,
        created=0,
    )
    store = load_paths([path], ReaderConfig())
    assert store.cache_accounting[0] == "embedded"
    summary = summarize(store, load_bundled())
    assert summary.cost_usd is not None
    assert any(
        "OTel cache read + cache write exceeded input_tokens on 1 call" in warning
        for warning in summary.warnings
    )
    assert "OTel input counts include cache reads and cache writes" in summary.methodology
    assert "JSONL and Langfuse exclude" not in summary.methodology
    written = write_report(
        store,
        analyze(store, load_bundled()),
        ReportOptions(out=tmp_path / "out", footer=False),
    )
    report = written.markdown.read_text(encoding="utf-8")
    assert "exceeded input_tokens on 1 call" in report


def test_jsonl_anthropic_methodology_excludes_cache_tokens(tmp_path: Path) -> None:
    from tracetally.pricing.table import load_bundled

    path = _jsonl(
        tmp_path,
        [
            _call(
                "claude-sonnet-4-5",
                provider="anthropic",
                input_tokens=1000,
                cached_input_tokens=400,
                cache_creation_input_tokens=100,
            )
        ],
    )
    summary = summarize(load_paths([path], ReaderConfig()), load_bundled())
    assert "exclude cache reads and cache writes" in summary.methodology
    assert "OTel input counts include" not in summary.methodology


def test_unknown_price_table_key_is_named(tmp_path: Path) -> None:
    prices = _price_file(tmp_path, {"gpt-4o": _model("openai", "1", "0")})
    config = tmp_path / "tracetally.toml"
    config.write_text(
        "\n".join(
            [
                "[prices.gpt-4o]",
                'provider = "openai"',
                'input = "1"',
                'output = "2"',
                'surprise = "9"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    path = _jsonl(tmp_path, [_call("gpt-4o", input_tokens=1)])
    result = _summarize(path, prices, config=config)
    assert any("unknown key 'prices.gpt-4o.surprise'" in warning for warning in result.warnings)


def test_unknown_toml_key_is_named(tmp_path: Path) -> None:
    prices = _price_file(tmp_path, {"gpt-4o": _model("openai", "1", "0")})
    config = tmp_path / "tracetally.toml"
    config.write_text('surprise = "1"\ncurrency = "USD"\n', encoding="utf-8")
    path = _jsonl(tmp_path, [_call("gpt-4o", input_tokens=1)])
    result = _summarize(path, prices, config=config)
    assert any("unknown key 'surprise'" in warning for warning in result.warnings)


def test_currency_header_and_findings_record_fx(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from tracetally.analysis.engine import analyze
    from tracetally.report.render import ReportOptions, write_report

    prices = _price_file(tmp_path, {"gpt-4o": _model("openai", "2", "0")})
    config = tmp_path / "tracetally.toml"
    config.write_text('currency = "EUR"\nfx_rate = "0.5"\n', encoding="utf-8")
    path = _jsonl(tmp_path, [_call("gpt-4o")])
    table = table_from_bytes(prices.read_bytes(), config=load_pricing_config(config))
    store = load_paths([path], ReaderConfig())
    summary = summarize(store, table)
    assert "Cost EUR" in summary.to_text()
    written = write_report(
        store,
        analyze(store, table),
        ReportOptions(
            out=tmp_path / "out",
            redact=False,
            include_content=False,
            content_requested=False,
            footer=False,
            generated_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
        ),
    )
    markdown = written.markdown.read_text(encoding="utf-8")
    html = written.html.read_text(encoding="utf-8")
    lines = markdown.splitlines()
    assert any(line.startswith("- Observed spend:") for line in lines)
    assert any(line.startswith("- Billed:") for line in lines)
    assert "Cost EUR" in markdown
    assert "Cost EUR" in html
    findings = json.loads(written.findings.read_text(encoding="utf-8"))
    assert findings["currency"] == "EUR"
    assert findings["fx_rate"] == "0.5"


def test_prices_flag_is_not_in_readme_docs_or_help() -> None:
    from typer.testing import CliRunner

    from tracetally.cli import app

    root = Path(__file__).resolve().parents[1]
    assert "--prices" not in (root / "README.md").read_text(encoding="utf-8")
    docs = root / "docs"
    for path in docs.rglob("*"):
        if path.is_file():
            assert "--prices" not in path.read_text(encoding="utf-8")
    runner = CliRunner()
    for args in (["--help"], ["summary", "--help"], ["analyze", "--help"], ["report", "--help"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0
        assert "--prices" not in result.output


def test_batch_priority_flex_only_when_export_shows_tier(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {
            "tiered": _model(
                "openai",
                "10",
                "0",
                input_cost_per_token_batches=_per_token("4"),
                output_cost_per_token_batches=_per_token("0"),
                input_cost_per_token_priority=_per_token("20"),
                output_cost_per_token_priority=_per_token("0"),
                input_cost_per_token_flex=_per_token("2"),
                output_cost_per_token_flex=_per_token("0"),
            )
        },
    )

    def cost_for(**extra: object) -> Decimal:
        name = f"{extra.get('record_id', 'row')}.jsonl"
        path = _jsonl(tmp_path, [_call("tiered", **extra)], name=name)
        amount = _summarize(path, prices).cost_usd
        assert amount is not None
        return amount

    assert cost_for(record_id="standard") == Decimal("10")
    assert cost_for(record_id="batch", is_batch=True) == Decimal("4")
    assert cost_for(record_id="priority", service_tier="priority") == Decimal("20")
    assert cost_for(record_id="flex", service_tier="flex") == Decimal("2")


def test_discount_shows_list_and_effective(tmp_path: Path) -> None:
    prices = _price_file(tmp_path, {"gpt-4o": _model("openai", "4", "0")})
    config = tmp_path / "tracetally.toml"
    config.write_text('[discounts]\n"openai/gpt-4o" = "0.25"\n', encoding="utf-8")
    path = _jsonl(tmp_path, [_call("gpt-4o")])
    result = _summarize(path, prices, config=config)
    assert result.list_cost_usd == Decimal("4")
    assert result.cost_usd == Decimal("3")
    text = result.to_text()
    assert "list" in text and "effective" in text
    assert "4.000000" in text and "3.000000" in text


def test_billed_cost_beats_inferred(tmp_path: Path) -> None:
    prices = _price_file(tmp_path, {"gpt-4o": _model("openai", "1", "0")})
    path = _jsonl(
        tmp_path,
        [_call("gpt-4o", input_tokens=1_000_000, reported_cost_usd="7")],
    )
    result = _summarize(path, prices)
    assert result.cost_usd == Decimal("7")
    assert result.reported_cost_usd == Decimal("7")


def test_missing_token_counts_are_not_estimated(tmp_path: Path) -> None:
    from tracetally.core.schema import LLMCall, Status
    from tracetally.core.store import TraceStore

    prices = _price_file(tmp_path, {"gpt-4o-mini": _model("openai", "10", "20")})
    table = table_from_bytes(prices.read_bytes())
    unknown = LLMCall.model_construct(
        source="openai_csv",
        record_id="missing",
        endpoint="app",
        provider="openai",
        model="gpt-4o-mini",
        start_time=datetime(2026, 10, 1, tzinfo=timezone.utc),
        input_tokens=0,
        output_tokens=0,
        tokens_known=False,
        status=Status.OK,
        request_count=1,
    )
    store = TraceStore()
    store.append_call(unknown)
    result = summarize(store, table)
    assert result.cost_usd is None
    assert result.priced_calls == 0
    assert "not estimated" in " ".join(result.warnings)

    billed = tmp_path / "billed.csv"
    billed.write_text(
        "start_time,model,cost\n2026-10-01T00:00:00Z,gpt-4o-mini,4.50\n",
        encoding="utf-8",
    )
    loaded = load_paths([billed], ReaderConfig())
    assert int(loaded.input_tokens[0]) == 0
    assert int(loaded.output_tokens[0]) == 0
    assert loaded.tokens_known[0] is False
    billed_result = summarize(loaded, table)
    assert billed_result.cost_usd == Decimal("4.50")
    assert "no token counts" in " ".join(loaded.warnings)


def test_self_hosted_names_and_loopback_are_cost_unknown(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {
            "gpt-4o": _model("openai", "2", "0"),
            "llama": _model("vllm", "2", "0"),
        },
    )
    path = _jsonl(
        tmp_path,
        [
            _call("llama", provider="vllm"),
            _call("llama", provider="tgi", record_id="tgi"),
            _call("llama", provider="nim", record_id="nim"),
            _call("meta-llama/Llama-3-8B", provider="huggingface", record_id="hf"),
            _call("gpt-4o", base_url="http://127.0.0.1:8000", record_id="local"),
            _call("gpt-4o", record_id="cloud"),
        ],
    )
    result = _summarize(path, prices)
    assert result.priced_calls == 1
    assert result.unpriced_calls == 5
    assert result.cost_usd == Decimal("2")
    assert "cost unknown" in " ".join(result.warnings)


def test_user_alias_prices_azure_deployment(tmp_path: Path) -> None:
    prices = _price_file(
        tmp_path,
        {
            "gpt-4o": _model("openai", "2", "0"),
            "azure/gpt-4o": _model("azure", "9", "0"),
        },
    )
    config = tmp_path / "tracetally.toml"
    config.write_text('[aliases]\n"my-deploy" = "gpt-4o"\n', encoding="utf-8")
    path = _jsonl(tmp_path, [_call("my-deploy", provider="azure")])
    result = _summarize(path, prices, config=config)
    assert result.cost_usd == Decimal("9")
    assert result.by_model[0].price_match == "user_alias"


def test_fx_rate_is_static(tmp_path: Path) -> None:
    prices = _price_file(tmp_path, {"gpt-4o": _model("openai", "2", "0")})
    config = tmp_path / "tracetally.toml"
    config.write_text('currency = "EUR"\nfx_rate = "0.5"\n', encoding="utf-8")
    path = _jsonl(tmp_path, [_call("gpt-4o")])
    result = _summarize(path, prices, config=config)
    assert result.currency == "EUR"
    assert result.cost_usd == Decimal("1")


def test_report_shows_price_source_and_date(tmp_path: Path) -> None:
    from tracetally.analysis.engine import analyze
    from tracetally.pricing.table import load_bundled
    from tracetally.report.render import ReportOptions, write_report

    path = _jsonl(tmp_path, [_call("gpt-4o-mini", input_tokens=10, output_tokens=1)])
    table = load_bundled()
    store = load_paths([path], ReaderConfig())
    written = write_report(
        store,
        analyze(store, table),
        ReportOptions(
            out=tmp_path / "out",
            redact=False,
            include_content=False,
            content_requested=False,
            footer=True,
            generated_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
        ),
    )
    markdown = written.markdown.read_text(encoding="utf-8")
    html = written.html.read_text(encoding="utf-8")
    findings = json.loads(written.findings.read_text(encoding="utf-8"))
    for document in (markdown, html):
        assert "bundled" in document
        assert table.provenance.url in document
        assert table.retrieved_on.isoformat() in document
        assert table.provenance.sha256 in document
        assert "priced share" in document
    assert findings["price_source"] == "bundled"
    assert findings["price_url"] == table.provenance.url
    assert findings["price_table_retrieved_on"] == table.retrieved_on.isoformat()
    assert findings["price_sha256"] == table.provenance.sha256
    assert findings["priced_share"]
