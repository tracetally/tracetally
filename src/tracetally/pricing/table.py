# SPDX-License-Identifier: Apache-2.0
"""Load, validate, and resolve list prices.

Resolution order for a model id, within the named provider only:

1. exact id
2. alias
3. id with one trailing date suffix removed (``-YYYY-MM-DD``, ``-YYYYMMDD``, ``@YYYY…``),
   then exact or alias

There is no prefix match. An unknown id stays unresolved so a newer model is
never silently billed as an older one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from importlib.resources import files
from pathlib import Path

import yaml

from tracetally.core.errors import PriceError
from tracetally.core.money import decimal_price, token_cost

_DATE_SUFFIXES = (
    re.compile(r"-\d{4}-\d{2}-\d{2}$"),
    re.compile(r"-\d{8}$"),
    re.compile(r"@\d{4}.*$"),
)
_BUNDLED_NAME = "prices-2026-10.yaml"

# gen_ai.provider.name / gen_ai.system values that share one list-price table.
# Azure and Vertex are intentionally absent: their invoices are not these lists.
_PROVIDER_ALIASES = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "google": "gemini",
    "gcp.gemini": "gemini",
    "google-gemini": "gemini",
}


@dataclass(frozen=True)
class ModelPrice:
    provider: str
    model: str
    input: Decimal
    output: Decimal
    cached_input: Decimal | None
    source_url: str
    retrieved_on: date
    reasoning: Decimal | None = None
    aliases: tuple[str, ...] = ()
    long_context_above_tokens: int | None = None
    long_input: Decimal | None = None
    long_cached_input: Decimal | None = None
    long_output: Decimal | None = None
    caveat: str | None = None
    batch_discount: Decimal | None = None
    match: str = "exact"

    def with_match(self, match: str) -> ModelPrice:
        return ModelPrice(
            provider=self.provider,
            model=self.model,
            input=self.input,
            output=self.output,
            cached_input=self.cached_input,
            source_url=self.source_url,
            retrieved_on=self.retrieved_on,
            reasoning=self.reasoning,
            aliases=self.aliases,
            long_context_above_tokens=self.long_context_above_tokens,
            long_input=self.long_input,
            long_cached_input=self.long_cached_input,
            long_output=self.long_output,
            caveat=self.caveat,
            batch_discount=self.batch_discount,
            match=match,
        )

    def rates(self, input_tokens: int) -> tuple[Decimal, Decimal, Decimal]:
        """Return ``(input, cached_input, output)`` per 1M tokens.

        Cached tokens use the cached price when one is published and the input
        price otherwise. That is "no cache discount", not an invented one.
        A published long-context tier applies only when ``input_tokens`` is
        strictly above the published threshold.
        """
        use_long = (
            self.long_context_above_tokens is not None
            and self.long_input is not None
            and self.long_output is not None
            and input_tokens > self.long_context_above_tokens
        )
        if use_long:
            assert self.long_input is not None and self.long_output is not None
            cached = (
                self.long_cached_input if self.long_cached_input is not None else self.long_input
            )
            return self.long_input, cached, self.long_output
        cached = self.cached_input if self.cached_input is not None else self.input
        return self.input, cached, self.output

    def cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        is_batch: bool = False,
    ) -> tuple[Decimal, bool]:
        """Return ``(cost, batch_discount_applied)``.

        When the call is batched and this provider has no published batch
        discount, the standard price is returned and the flag is false.
        """
        cached = min(max(cached_input_tokens, 0), input_tokens)
        uncached = input_tokens - cached
        input_price, cached_price, output_price = self.rates(input_tokens)
        amount = (
            token_cost(uncached, input_price)
            + token_cost(cached, cached_price)
            + token_cost(output_tokens, output_price)
        )
        applied = False
        if is_batch and self.batch_discount is not None:
            amount *= self.batch_discount
            applied = True
        return amount, applied


@dataclass
class ProviderPrices:
    name: str
    models: dict[str, ModelPrice]
    aliases: dict[str, str]
    batch_discount: Decimal | None
    batch_source_url: str | None
    batch_retrieved_on: date | None

    def resolve(self, model: str) -> ModelPrice | None:
        found = self._lookup(model)
        if found is None:
            return None
        price, match = found
        if match == "exact":
            return price
        return price.with_match(match)

    def _lookup(self, model: str) -> tuple[ModelPrice, str] | None:
        direct = self.models.get(model)
        if direct is not None:
            return direct, "exact"
        alias = self.aliases.get(model)
        if alias is not None:
            return self.models[alias], "alias"
        for pattern in _DATE_SUFFIXES:
            matched = pattern.search(model)
            if not matched:
                continue
            stem = model[: matched.start()]
            if not stem:
                continue
            stemmed = self.models.get(stem)
            if stemmed is not None:
                return stemmed, "date_suffix"
            alias_stem = self.aliases.get(stem)
            if alias_stem is not None:
                return self.models[alias_stem], "date_suffix"
        return None


@dataclass
class PriceTable:
    schema_version: int
    table_id: str
    currency: str
    unit: str
    retrieved_on: date
    providers: dict[str, ProviderPrices]
    origin: str
    override_id: str | None = None
    notes: str | None = None
    _provider_index: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._provider_index = dict(_PROVIDER_ALIASES)

    @property
    def display_id(self) -> str:
        if self.override_id:
            return f"{self.table_id}+{self.override_id}"
        return self.table_id

    def canonical_provider(self, provider: str) -> str:
        key = provider.strip().casefold()
        return self._provider_index.get(key, key)

    def resolve(self, provider: str, model: str) -> ModelPrice | None:
        canonical = self.canonical_provider(provider)
        bucket = self.providers.get(canonical)
        if bucket is None:
            return None
        return bucket.resolve(model.strip())

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
                f"price table {self.table_id} was retrieved on {self.retrieved_on.isoformat()} "
                f"({age} days before {today.isoformat()}; max {max_age_days})"
            )

    def merge(self, override: PriceTable) -> PriceTable:
        """User prices replace bundled entries. Providers the user omits stay."""
        merged: dict[str, ProviderPrices] = {
            name: ProviderPrices(
                name=bucket.name,
                models=dict(bucket.models),
                aliases=dict(bucket.aliases),
                batch_discount=bucket.batch_discount,
                batch_source_url=bucket.batch_source_url,
                batch_retrieved_on=bucket.batch_retrieved_on,
            )
            for name, bucket in self.providers.items()
        }
        for name, bucket in override.providers.items():
            current = merged.get(name)
            if current is None:
                merged[name] = bucket
                continue
            for model_name, price in bucket.models.items():
                _drop_aliases(current, model_name)
                current.models[model_name] = price
            for alias, target in bucket.aliases.items():
                current.aliases[alias] = target
            if bucket.batch_discount is not None:
                current.batch_discount = bucket.batch_discount
                current.batch_source_url = bucket.batch_source_url
                current.batch_retrieved_on = bucket.batch_retrieved_on
            _rebind_batch(current)
        return PriceTable(
            schema_version=self.schema_version,
            table_id=self.table_id,
            currency=self.currency,
            unit=self.unit,
            retrieved_on=self.retrieved_on,
            providers=merged,
            origin=f"{self.origin} + {override.origin}",
            override_id=override.table_id,
            notes=override.notes or self.notes,
        )


def load_prices(path: Path | None = None) -> PriceTable:
    bundled = load_bundled()
    if path is None:
        return bundled
    if not path.is_file():
        raise PriceError(f"price file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PriceError(f"price file is not valid UTF-8: {path}") from exc
    override = parse_price_text(text, origin=str(path))
    return bundled.merge(override)


def load_bundled() -> PriceTable:
    resource = files("tracetally.pricing.data").joinpath(_BUNDLED_NAME)
    try:
        text = resource.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PriceError(f"bundled price table {_BUNDLED_NAME} is not packaged") from exc
    return parse_price_text(text, origin=f"bundled:{_BUNDLED_NAME}")


def parse_price_text(text: str, *, origin: str) -> PriceTable:
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PriceError(f"{origin}: invalid YAML ({exc})") from exc
    if not isinstance(loaded, dict):
        raise PriceError(f"{origin}: price file must be a mapping")
    return _parse_document(loaded, origin=origin)


def _parse_document(doc: dict[str, object], *, origin: str) -> PriceTable:
    schema_version = doc.get("schema_version")
    if schema_version != 1:
        raise PriceError(f"{origin}: schema_version must be 1")
    table_id = _require_str(doc, "table_id", origin)
    currency = _require_str(doc, "currency", origin)
    unit = _require_str(doc, "unit", origin)
    if currency != "USD":
        raise PriceError(f"{origin}: currency must be USD")
    if unit != "per_1m_tokens":
        raise PriceError(f"{origin}: unit must be per_1m_tokens")
    retrieved = _require_date(doc, "retrieved_on", origin)
    providers_raw = doc.get("providers")
    if not isinstance(providers_raw, dict) or not providers_raw:
        raise PriceError(f"{origin}: providers must be a non-empty mapping")
    providers: dict[str, ProviderPrices] = {}
    for name, raw in providers_raw.items():
        provider_name = str(name).strip().casefold()
        if not isinstance(raw, dict):
            raise PriceError(f"{origin}: provider {provider_name!r} must be a mapping")
        providers[provider_name] = _parse_provider(provider_name, raw, origin)
    notes = doc.get("notes")
    return PriceTable(
        schema_version=1,
        table_id=table_id,
        currency=currency,
        unit=unit,
        retrieved_on=retrieved,
        providers=providers,
        origin=origin,
        notes=str(notes) if isinstance(notes, str) else None,
    )


def _parse_provider(name: str, raw: dict[str, object], origin: str) -> ProviderPrices:
    where = f"{origin}: provider {name!r}"
    discount, source, retrieved = _parse_batch(raw.get("batch_discount"), where)
    models_raw = raw.get("models")
    if not isinstance(models_raw, dict) or not models_raw:
        raise PriceError(f"{where}: models must be a non-empty mapping")
    models: dict[str, ModelPrice] = {}
    aliases: dict[str, str] = {}
    for model_name, spec in models_raw.items():
        model = str(model_name).strip()
        if not isinstance(spec, dict):
            raise PriceError(f"{where}: model {model!r} must be a mapping")
        price = _parse_model(name, model, spec, where, discount)
        if model in models:
            raise PriceError(f"{where}: duplicate model {model!r}")
        models[model] = price
        for alias in price.aliases:
            if alias in models or alias in aliases:
                raise PriceError(f"{where}: alias {alias!r} collides with another model")
            aliases[alias] = model
    return ProviderPrices(
        name=name,
        models=models,
        aliases=aliases,
        batch_discount=discount,
        batch_source_url=source,
        batch_retrieved_on=retrieved,
    )


def _parse_batch(raw: object, where: str) -> tuple[Decimal | None, str | None, date | None]:
    if raw is None:
        return None, None, None
    if not isinstance(raw, dict):
        raise PriceError(f"{where}: batch_discount must be a mapping")
    if "value" not in raw:
        raise PriceError(f"{where}: batch_discount.value is required")
    try:
        value = decimal_price(raw["value"])
    except ValueError as exc:
        raise PriceError(f"{where}: batch_discount.value: {exc}") from exc
    if value < 0 or value > 1:
        raise PriceError(f"{where}: batch_discount.value must be between 0 and 1")
    source = _require_str(raw, "source_url", f"{where}: batch_discount")
    _check_url(source, f"{where}: batch_discount")
    retrieved = _require_date(raw, "retrieved_on", f"{where}: batch_discount")
    return value, source, retrieved


def _parse_model(
    provider: str,
    model: str,
    spec: dict[str, object],
    where: str,
    batch_discount: Decimal | None,
) -> ModelPrice:
    loc = f"{where}: model {model!r}"
    input_price = _price_field(spec, "input", loc)
    output_price = _price_field(spec, "output", loc)
    cached_price = _optional_price(spec, "cached_input", loc)
    reasoning_price = _optional_price(spec, "reasoning", loc)
    source = _require_str(spec, "source_url", loc)
    _check_url(source, loc)
    retrieved = _require_date(spec, "retrieved_on", loc)
    aliases_raw = spec.get("aliases") or []
    if not isinstance(aliases_raw, list) or not all(
        isinstance(item, str) and item for item in aliases_raw
    ):
        raise PriceError(f"{loc}: aliases must be a list of strings")
    aliases = tuple(str(item) for item in aliases_raw)
    long_above, long_input, long_cached, long_output = _parse_long(spec, loc)
    caveat = spec.get("caveat")
    if caveat is not None and not isinstance(caveat, str):
        raise PriceError(f"{loc}: caveat must be a string")
    return ModelPrice(
        provider=provider,
        model=model,
        input=input_price,
        output=output_price,
        cached_input=cached_price,
        reasoning=reasoning_price,
        source_url=source,
        retrieved_on=retrieved,
        aliases=aliases,
        long_context_above_tokens=long_above,
        long_input=long_input,
        long_cached_input=long_cached,
        long_output=long_output,
        caveat=str(caveat) if isinstance(caveat, str) else None,
        batch_discount=batch_discount,
    )


def _optional_price(spec: dict[str, object], key: str, loc: str) -> Decimal | None:
    raw = spec.get(key)
    if raw is None:
        return None
    try:
        value = decimal_price(raw)
    except ValueError as exc:
        raise PriceError(f"{loc}: {key}: {exc}") from exc
    if value < 0:
        raise PriceError(f"{loc}: {key} must be >= 0")
    return value


def _price_field(spec: dict[str, object], key: str, loc: str) -> Decimal:
    if key not in spec:
        raise PriceError(f"{loc}: field {key!r} is required")
    try:
        value = decimal_price(spec[key])
    except ValueError as exc:
        raise PriceError(f"{loc}: field {key!r}: {exc}") from exc
    if value < 0:
        raise PriceError(f"{loc}: field {key!r} must be >= 0")
    return value


def _parse_long(
    spec: dict[str, object], loc: str
) -> tuple[int | None, Decimal | None, Decimal | None, Decimal | None]:
    keys = ("long_context_above_tokens", "long_input", "long_output", "long_cached_input")
    present = [key for key in keys if spec.get(key) is not None]
    if not present:
        return None, None, None, None
    if (
        "long_context_above_tokens" not in present
        or "long_input" not in present
        or "long_output" not in present
    ):
        raise PriceError(
            f"{loc}: long-context prices need a threshold, long_input, and long_output"
        )
    try:
        above = int(str(spec["long_context_above_tokens"]))
        long_input = decimal_price(spec["long_input"])
        long_output = decimal_price(spec["long_output"])
    except (TypeError, ValueError) as exc:
        raise PriceError(f"{loc}: invalid long-context price ({exc})") from exc
    if above < 1 or long_input < 0 or long_output < 0:
        raise PriceError(f"{loc}: long-context threshold and prices must be positive")
    cached_raw = spec.get("long_cached_input")
    long_cached: Decimal | None = None
    if cached_raw is not None:
        try:
            long_cached = decimal_price(cached_raw)
        except ValueError as exc:
            raise PriceError(f"{loc}: long_cached_input: {exc}") from exc
        if long_cached < 0:
            raise PriceError(f"{loc}: long_cached_input must be >= 0")
    return above, long_input, long_cached, long_output


def _require_str(doc: dict[str, object], key: str, where: str) -> str:
    value = doc.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PriceError(f"{where}: field {key!r} is required")
    return value.strip()


def _require_date(doc: dict[str, object], key: str, where: str) -> date:
    value = doc.get(key)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise PriceError(f"{where}: field {key!r} must be YYYY-MM-DD") from exc
    raise PriceError(f"{where}: field {key!r} must be YYYY-MM-DD")


def _check_url(url: str, where: str) -> None:
    if not (url.startswith("https://") or url.startswith("http://")):
        raise PriceError(f"{where}: source_url must be an http(s) URL")


def _drop_aliases(bucket: ProviderPrices, model_name: str) -> None:
    stale = [alias for alias, target in bucket.aliases.items() if target == model_name]
    for alias in stale:
        del bucket.aliases[alias]


def _rebind_batch(bucket: ProviderPrices) -> None:
    rebound: dict[str, ModelPrice] = {}
    for name, price in bucket.models.items():
        if price.batch_discount == bucket.batch_discount:
            rebound[name] = price
            continue
        rebound[name] = ModelPrice(
            provider=price.provider,
            model=price.model,
            input=price.input,
            output=price.output,
            cached_input=price.cached_input,
            source_url=price.source_url,
            retrieved_on=price.retrieved_on,
            reasoning=price.reasoning,
            aliases=price.aliases,
            long_context_above_tokens=price.long_context_above_tokens,
            long_input=price.long_input,
            long_cached_input=price.long_cached_input,
            long_output=price.long_output,
            caveat=price.caveat,
            batch_discount=bucket.batch_discount,
            match=price.match,
        )
    bucket.models = rebound
