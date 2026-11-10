# SPDX-License-Identifier: Apache-2.0
"""Parse LiteLLM's model price JSON. No network."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from importlib.resources import files

from tracetally.core.errors import PriceError

BUNDLED_NAME = "model_prices_and_context_window.json"
BUNDLED_SNAPSHOT_DATE = date(2026, 11, 10)
_MILLION = Decimal(1_000_000)
_SKIP_KEYS = frozenset({"sample_spec"})
_ABOVE = re.compile(r"above_(\d+)k_tokens")


@dataclass(frozen=True)
class TierRates:
    """Per-1M-token rates for one service tier.

    ``None`` means that column was not published. A standard card always has
    input and output. Other tiers may publish only some columns; the quote
    fills the gaps from the standard card rather than with zero.
    """

    input: Decimal | None
    output: Decimal | None
    cached: Decimal | None = None
    creation: Decimal | None = None
    long_input: Decimal | None = None
    long_output: Decimal | None = None
    long_cached: Decimal | None = None
    long_creation: Decimal | None = None


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    provider: str
    standard: TierRates | None
    batch: TierRates | None
    priority: TierRates | None
    flex: TierRates | None
    alias_of: str | None
    zero_price: bool
    other_thresholds: tuple[int, ...] = ()

    @property
    def has_rates(self) -> bool:
        return self.standard is not None


Catalog = dict[str, CatalogEntry]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bundled_bytes() -> bytes:
    resource = files("tracetally.pricing.data").joinpath(BUNDLED_NAME)
    try:
        return resource.read_bytes()
    except FileNotFoundError as exc:
        raise PriceError(f"bundled price snapshot {BUNDLED_NAME} is not packaged") from exc


def parse_price_bytes(data: bytes) -> Catalog:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PriceError("price file is not valid UTF-8") from exc
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PriceError(f"price file is not valid JSON ({exc.msg})") from exc
    return parse_price_document(loaded)


def parse_price_document(loaded: object) -> Catalog:
    if not isinstance(loaded, dict):
        raise PriceError("price file schema changed: expected a JSON object of models")
    catalog: Catalog = {}
    usable = 0
    for key, raw in loaded.items():
        name = str(key)
        if name in _SKIP_KEYS:
            continue
        if not isinstance(raw, dict):
            raise PriceError("price file schema changed: model entries must be objects")
        entry = _parse_entry(name, raw)
        if entry is None:
            continue
        catalog[name] = entry
        usable += 1
    if usable < 1:
        raise PriceError("price file schema changed: no model prices found")
    return catalog


def _parse_entry(key: str, raw: dict[str, object]) -> CatalogEntry | None:
    alias = raw.get("alias_of")
    alias_of = alias.strip() if isinstance(alias, str) and alias.strip() else None
    if alias is not None and alias_of is None:
        raise PriceError("price file schema changed: alias_of must be a string")
    provider = raw.get("litellm_provider")
    provider_name = provider.strip().casefold() if isinstance(provider, str) else ""
    standard = _tier(raw, "")
    if standard is None and alias_of is None:
        return None
    if standard is None and alias_of is not None:
        return CatalogEntry(
            key=key,
            provider=provider_name,
            standard=None,
            batch=None,
            priority=None,
            flex=None,
            alias_of=alias_of,
            zero_price=False,
        )
    assert standard is not None and standard.input is not None and standard.output is not None
    batch = _tier(raw, "_batches")
    priority = _tier(raw, "_priority")
    flex = _tier(raw, "_flex")
    zero = standard.input == 0 and standard.output == 0
    return CatalogEntry(
        key=key,
        provider=provider_name,
        standard=standard,
        batch=batch,
        priority=priority,
        flex=flex,
        alias_of=alias_of,
        zero_price=zero,
        other_thresholds=_other_thresholds(raw),
    )


def _tier(raw: dict[str, object], suffix: str) -> TierRates | None:
    incoming = _per_million(raw.get(f"input_cost_per_token{suffix}"))
    outgoing = _per_million(raw.get(f"output_cost_per_token{suffix}"))
    cached = _per_million(raw.get(f"cache_read_input_token_cost{suffix}"))
    creation = _per_million(raw.get(f"cache_creation_input_token_cost{suffix}"))
    long_in = _per_million(raw.get(f"input_cost_per_token_above_200k_tokens{suffix}"))
    long_out = _per_million(raw.get(f"output_cost_per_token_above_200k_tokens{suffix}"))
    long_cached = _per_million(raw.get(f"cache_read_input_token_cost_above_200k_tokens{suffix}"))
    long_creation = _per_million(
        raw.get(f"cache_creation_input_token_cost_above_200k_tokens{suffix}")
    )
    if (
        incoming is None
        and outgoing is None
        and cached is None
        and creation is None
        and long_in is None
        and long_out is None
        and long_cached is None
        and long_creation is None
    ):
        return None
    if suffix == "" and (incoming is None or outgoing is None):
        return None
    _reject_negative(
        incoming, outgoing, cached, creation, long_in, long_out, long_cached, long_creation
    )
    return TierRates(
        input=incoming,
        output=outgoing,
        cached=cached,
        creation=creation,
        long_input=long_in,
        long_output=long_out,
        long_cached=long_cached,
        long_creation=long_creation,
    )


def _other_thresholds(raw: dict[str, object]) -> tuple[int, ...]:
    """Token thresholds other than 200k that this entry publishes a rate for.

    The cost engine applies only the 200k tier exactly. Crossing one of these
    still has to be visible, so the call is labelled approximate.
    """
    found: set[int] = set()
    for key, value in raw.items():
        if value is None or "cost" not in key:
            continue
        matched = _ABOVE.search(key)
        if matched is None:
            continue
        thousand = int(matched.group(1))
        if thousand == 200:
            continue
        found.add(thousand * 1000)
    return tuple(sorted(found))


def _per_million(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PriceError("price file schema changed: token costs must be numbers")
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise PriceError("price file schema changed: token costs must be finite")
    return parsed * _MILLION


def _reject_negative(*values: Decimal | None) -> None:
    for value in values:
        if value is not None and value < 0:
            raise PriceError("price file schema changed: token costs must be >= 0")
