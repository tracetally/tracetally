# SPDX-License-Identifier: Apache-2.0
"""List prices from the LiteLLM snapshot, with local overrides from tracetally.toml."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from tracetally.core.errors import PriceError
from tracetally.core.money import token_cost
from tracetally.pricing.catalog import (
    Catalog,
    CatalogEntry,
    TierRates,
    parse_price_bytes,
    sha256_hex,
)
from tracetally.pricing.config import PricingConfig, load_pricing_config
from tracetally.pricing.fetch import LITELLM_PRICES_URL
from tracetally.pricing.match import Match, canon, cost_unknown_reason, resolve_match
from tracetally.pricing.source import PriceDocument, Provenance, acquire_prices

BatchCachedMode = Literal["stack", "standard"]
_LONG_CONTEXT_TOKENS = 200_000


@dataclass(frozen=True)
class ModelPrice:
    provider: str
    model: str
    input: Decimal
    output: Decimal
    cached_input: Decimal | None
    source_url: str
    retrieved_on: date
    standard_rates: TierRates
    batch_rates: TierRates | None = None
    priority_rates: TierRates | None = None
    flex_rates: TierRates | None = None
    reasoning: Decimal | None = None
    aliases: tuple[str, ...] = ()
    long_context_above_tokens: int | None = None
    long_input: Decimal | None = None
    long_cached_input: Decimal | None = None
    long_output: Decimal | None = None
    caveat: str | None = None
    batch_discount: Decimal | None = None
    batch_cached: BatchCachedMode = "standard"
    match: str = "exact"
    label: str = ""
    discount: Decimal = Decimal(0)
    fx_rate: Decimal = Decimal(1)
    other_thresholds: tuple[int, ...] = ()

    def exceeds_unapplied_threshold(self, sized_tokens: int) -> bool:
        """True when a non-200k long-context rate exists and this request is past it."""
        return any(sized_tokens > limit for limit in self.other_thresholds)

    def with_match(self, match: str) -> ModelPrice:
        return replace(self, match=match)

    def rates(self, input_tokens: int) -> tuple[Decimal, Decimal, Decimal]:
        """``(input, cached_input, output)`` per 1M tokens for the standard tier."""
        incoming, cached, _creation, outgoing = _quote(self.standard_rates, None, input_tokens)
        return incoming, cached, outgoing

    def list_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cache_creation_tokens: int = 0,
        cache_accounting: str = "included",
        is_batch: bool = False,
        tier: str | None = None,
    ) -> Decimal:
        """Price before a configured discount. FX is already applied."""
        amount, _applied = replace(self, discount=Decimal(0)).cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_accounting=cache_accounting,
            is_batch=is_batch,
            tier=tier,
        )
        return amount if amount is not None else Decimal(0)

    def cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cache_creation_tokens: int = 0,
        cache_accounting: str = "included",
        is_batch: bool = False,
        tier: str | None = None,
    ) -> tuple[Decimal | None, bool]:
        """Return ``(cost, batch_rate_applied)``.

        ``cache_accounting`` says whether ``input_tokens`` already contains
        cache reads. Subtraction happens only for ``included``, and only here.
        Cache writes are priced at the published creation rate. A missing
        creation rate does not become the input rate or zero: the cost is
        unknown. The over-200k rate applies only to this request. Batch,
        priority, and flex rates apply only when that tier was requested.
        """
        uncached, read, write, sized = _cache_buckets(
            input_tokens,
            cached_input_tokens,
            cache_creation_tokens,
            cache_accounting,
        )
        selected, applied = _selected_tier(self, is_batch=is_batch, tier=tier)
        card = _tier_card(self, selected)
        input_rate, cached_rate, creation_rate, output_rate = _quote(
            self.standard_rates, card, sized
        )
        if write and creation_rate is None:
            return None, False
        amount = (
            token_cost(uncached, input_rate)
            + token_cost(read, cached_rate)
            + token_cost(write, creation_rate or Decimal(0))
            + token_cost(output_tokens, output_rate)
        )
        if self.discount:
            amount *= Decimal(1) - self.discount
        if self.fx_rate != 1:
            amount *= self.fx_rate
        return amount, applied


@dataclass
class ProviderPrices:
    name: str
    models: dict[str, ModelPrice]
    aliases: dict[str, str]
    batch_discount: Decimal | None
    batch_source_url: str | None
    batch_retrieved_on: date | None
    batch_cached: BatchCachedMode
    batch_cached_source_url: str | None = None
    batch_cached_retrieved_on: date | None = None


@dataclass(frozen=True)
class PriceDecision:
    price: ModelPrice | None
    step: str | None
    label: str
    status: Literal["priced", "unknown", "cost_unknown"]
    reason: str | None


@dataclass
class PriceTable:
    schema_version: int
    table_id: str
    currency: str
    unit: str
    retrieved_on: date
    providers: dict[str, ProviderPrices]
    origin: str
    provenance: Provenance
    config: PricingConfig = field(default_factory=PricingConfig)
    _catalog: Catalog = field(default_factory=dict)

    @property
    def display_id(self) -> str:
        base = f"litellm:{self.provenance.source}"
        if self.config.active:
            return f"{base}+toml"
        return base

    @property
    def discounts_active(self) -> bool:
        if self.config.run_discount not in (None, Decimal(0)):
            return True
        return any(value != 0 for value in self.config.discounts.values())

    def canonical_provider(self, provider: str) -> str:
        return canon(provider)

    def resolve(
        self,
        provider: str,
        model: str,
        *,
        base_url: str | None = None,
    ) -> ModelPrice | None:
        return self.decide(provider, model, base_url=base_url).price

    def decide(
        self,
        provider: str,
        model: str,
        *,
        base_url: str | None = None,
    ) -> PriceDecision:
        reason = cost_unknown_reason(provider, model, base_url)
        if reason is not None:
            return PriceDecision(None, None, "", "cost_unknown", reason)
        matched = resolve_match(self._catalog, provider, model, self.config.aliases)
        if matched is None:
            return PriceDecision(None, None, "", "unknown", None)
        if matched.entry.zero_price:
            return PriceDecision(
                None,
                matched.step,
                matched.label,
                "cost_unknown",
                "list price is zero",
            )
        return PriceDecision(
            _decorate(matched, self.provenance, self.config, provider, model),
            matched.step,
            matched.label,
            "priced",
            None,
        )

    def iter_models(self) -> list[ModelPrice]:
        rows: list[ModelPrice] = []
        for provider in sorted(self.providers):
            bucket = self.providers[provider]
            for model in sorted(bucket.models):
                rows.append(bucket.models[model])
        return rows

    def age_days(self, today: date) -> int:
        return (today - self.retrieved_on).days

    def assert_fresh(self, today: date, *, max_age_days: int = 60) -> None:
        """Release-time reminder. Not called on the summary path."""
        age = self.age_days(today)
        if age > max_age_days:
            raise PriceError(
                f"price table {self.display_id} was retrieved on {self.retrieved_on.isoformat()} "
                f"({age} days before {today.isoformat()}; max {max_age_days})"
            )


def load_bundled() -> PriceTable:
    """Bundled snapshot only. Does not read config and does not open a socket."""
    document = acquire_prices(offline=True)
    return _build(document, PricingConfig())


def load_prices(
    *,
    offline: bool = False,
    pricing_config: Path | None = None,
    discount: Decimal | None = None,
    cache_path: Path | None = None,
) -> PriceTable:
    """Live, cache, or bundled prices, then tracetally.toml overrides.

    ``offline`` skips the network and ignores the cache. A failure while
    fetching never raises: the loader falls back to a stale cache, then the
    bundled snapshot.
    """
    config = load_pricing_config(pricing_config).with_run_discount(discount)
    document = acquire_prices(offline=offline, cache_path=cache_path)
    return _build(document, config)


def table_from_bytes(
    data: bytes,
    *,
    config: PricingConfig | None = None,
    retrieved_on: date | None = None,
) -> PriceTable:
    """Build a table from price JSON. Used by tests; not a network path."""
    chosen = config or PricingConfig()
    document = PriceDocument(
        catalog=parse_price_bytes(data),
        provenance=Provenance(
            source="bundled",
            url=LITELLM_PRICES_URL,
            retrieved_on=retrieved_on or date.today(),
            sha256=sha256_hex(data),
        ),
    )
    return _build(document, chosen)


def _build(document: PriceDocument, config: PricingConfig) -> PriceTable:
    catalog = document.catalog
    if config.prices:
        catalog = dict(catalog)
        for key, override in config.prices.items():
            catalog[key] = override.entry(key)
    providers: dict[str, ProviderPrices] = {}
    for entry in catalog.values():
        if not entry.has_rates or entry.standard is None or entry.standard.input is None:
            continue
        bucket_name = canon(entry.provider) or "unknown"
        price = _model_price(entry, document.provenance, bucket_name)
        bucket = providers.get(bucket_name)
        if bucket is None:
            bucket = ProviderPrices(
                name=bucket_name,
                models={},
                aliases={},
                batch_discount=None,
                batch_source_url=None,
                batch_retrieved_on=None,
                batch_cached="standard",
            )
            providers[bucket_name] = bucket
        bucket.models[entry.key] = price
        if price.batch_discount is not None and bucket.batch_source_url is None:
            bucket.batch_discount = price.batch_discount
            bucket.batch_source_url = document.provenance.url
            bucket.batch_retrieved_on = document.provenance.retrieved_on
            bucket.batch_cached = price.batch_cached
    return PriceTable(
        schema_version=1,
        table_id="litellm",
        currency=config.currency,
        unit="per_1m_tokens",
        retrieved_on=document.provenance.retrieved_on,
        providers=providers,
        origin=document.provenance.source,
        provenance=document.provenance,
        config=config,
        _catalog=catalog,
    )


def _decorate(
    matched: Match,
    provenance: Provenance,
    config: PricingConfig,
    provider: str,
    model: str,
) -> ModelPrice:
    entry = matched.entry
    billed = canon(provider)
    price = _model_price(entry, provenance, canon(entry.provider) or billed or "unknown")
    discount = config.discount_for_models(billed, model, entry.key)
    return replace(
        price,
        match=matched.step,
        label=matched.label,
        discount=discount,
        fx_rate=config.fx_rate,
    )


def _model_price(entry: CatalogEntry, provenance: Provenance, provider: str) -> ModelPrice:
    standard = entry.standard
    assert standard is not None and standard.input is not None and standard.output is not None
    discount, mode = _batch_meta(standard, entry.batch)
    long = (
        _LONG_CONTEXT_TOKENS
        if standard.long_input is not None and standard.long_output is not None
        else None
    )
    return ModelPrice(
        provider=provider,
        model=entry.key,
        input=standard.input,
        output=standard.output,
        cached_input=standard.cached,
        source_url=provenance.url,
        retrieved_on=provenance.retrieved_on,
        standard_rates=standard,
        batch_rates=entry.batch,
        priority_rates=entry.priority,
        flex_rates=entry.flex,
        long_context_above_tokens=long,
        long_input=standard.long_input,
        long_cached_input=standard.long_cached,
        long_output=standard.long_output,
        batch_discount=discount,
        batch_cached=mode,
        other_thresholds=entry.other_thresholds,
    )


def _batch_meta(
    standard: TierRates, batch: TierRates | None
) -> tuple[Decimal | None, BatchCachedMode]:
    if batch is None:
        return None, "standard"
    ratio: Decimal | None
    if batch.input is not None and standard.input not in (None, Decimal(0)):
        assert standard.input is not None
        ratio = batch.input / standard.input
    elif batch.output is not None and standard.output not in (None, Decimal(0)):
        assert standard.output is not None
        ratio = batch.output / standard.output
    else:
        ratio = Decimal(1)
    mode: BatchCachedMode = "standard"
    if batch.cached is not None and (standard.cached is None or batch.cached < standard.cached):
        mode = "stack"
    return ratio, mode


def _selected_tier(price: ModelPrice, *, is_batch: bool, tier: str | None) -> tuple[str, bool]:
    requested = (tier or "").strip().casefold()
    if is_batch or requested in {"batch", "batched"}:
        requested = "batch"
    elif requested not in {"priority", "flex"}:
        requested = "standard"
    if requested != "standard" and _tier_card(price, requested) is None:
        return "standard", False
    return requested, requested == "batch"


def _tier_card(price: ModelPrice, name: str) -> TierRates | None:
    if name == "batch":
        return price.batch_rates
    if name == "priority":
        return price.priority_rates
    if name == "flex":
        return price.flex_rates
    return None


def request_size(
    input_tokens: int,
    cached_input_tokens: int,
    cache_creation_tokens: int,
    cache_accounting: str,
) -> int:
    """Token count the long-context tier looks at for this one request."""
    return _cache_buckets(
        input_tokens, cached_input_tokens, cache_creation_tokens, cache_accounting
    )[3]


def _cache_buckets(
    input_tokens: int,
    cached_input_tokens: int,
    cache_creation_tokens: int,
    cache_accounting: str,
) -> tuple[int, int, int, int]:
    """Return ``(uncached_input, cache_read, cache_write, tier_size)``.

    ``tier_size`` is the request size the over-200k tier looks at. ``included``
    already counts cache reads inside ``input_tokens``. ``separate`` does not.
    ``absent`` ignores cache counters.
    """
    read = max(cached_input_tokens, 0)
    write = max(cache_creation_tokens, 0)
    if cache_accounting == "absent":
        return max(input_tokens, 0), 0, 0, max(input_tokens, 0)
    if cache_accounting == "included":
        read = min(read, max(input_tokens, 0))
        return max(input_tokens, 0) - read, read, write, max(input_tokens, 0) + write
    return max(input_tokens, 0), read, write, max(input_tokens, 0) + read + write


def _quote(
    standard: TierRates, tier: TierRates | None, input_tokens: int
) -> tuple[Decimal, Decimal, Decimal | None, Decimal]:
    chosen = tier if tier is not None else standard
    use_long = (
        input_tokens > _LONG_CONTEXT_TOKENS
        and chosen.long_input is not None
        and chosen.long_output is not None
    )
    source = chosen
    if (
        not use_long
        and input_tokens > _LONG_CONTEXT_TOKENS
        and standard.long_input is not None
        and standard.long_output is not None
    ):
        use_long = True
        source = standard
    if use_long:
        assert source.long_input is not None and source.long_output is not None
        cached = source.long_cached
        if cached is None:
            cached = standard.long_cached
        if cached is None:
            cached = source.long_input
        creation = source.long_creation
        if creation is None:
            creation = standard.long_creation
        if creation is None:
            creation = source.creation if source.creation is not None else standard.creation
        return source.long_input, cached, creation, source.long_output
    incoming = chosen.input if chosen.input is not None else standard.input
    outgoing = chosen.output if chosen.output is not None else standard.output
    assert incoming is not None and outgoing is not None
    cached = chosen.cached if chosen.cached is not None else standard.cached
    if cached is None:
        cached = incoming
    creation = chosen.creation if chosen.creation is not None else standard.creation
    return incoming, cached, creation, outgoing
