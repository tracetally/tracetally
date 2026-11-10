# SPDX-License-Identifier: Apache-2.0
"""``tracetally.toml`` overrides: aliases, prices, discounts, currency. No network."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from tracetally.core.errors import PriceError
from tracetally.core.money import decimal_price
from tracetally.pricing.catalog import CatalogEntry, TierRates


@dataclass(frozen=True)
class ModelOverride:
    provider: str
    input: Decimal
    output: Decimal
    cached: Decimal | None = None
    batch_input: Decimal | None = None
    batch_output: Decimal | None = None
    batch_cached: Decimal | None = None
    priority_input: Decimal | None = None
    priority_output: Decimal | None = None
    priority_cached: Decimal | None = None
    flex_input: Decimal | None = None
    flex_output: Decimal | None = None
    flex_cached: Decimal | None = None
    above_input: Decimal | None = None
    above_output: Decimal | None = None
    above_cached: Decimal | None = None

    def entry(self, key: str) -> CatalogEntry:
        provider = self.provider.strip().casefold()
        standard = TierRates(
            input=self.input,
            output=self.output,
            cached=self.cached,
            long_input=self.above_input,
            long_output=self.above_output,
            long_cached=self.above_cached,
        )
        return CatalogEntry(
            key=key,
            provider=provider,
            standard=standard,
            batch=_optional_tier(self.batch_input, self.batch_output, self.batch_cached),
            priority=_optional_tier(
                self.priority_input, self.priority_output, self.priority_cached
            ),
            flex=_optional_tier(self.flex_input, self.flex_output, self.flex_cached),
            alias_of=None,
            zero_price=self.input == 0 and self.output == 0,
        )


def _optional_tier(
    incoming: Decimal | None, outgoing: Decimal | None, cached: Decimal | None
) -> TierRates | None:
    if incoming is None and outgoing is None and cached is None:
        return None
    return TierRates(input=incoming, output=outgoing, cached=cached)


@dataclass(frozen=True)
class PricingConfig:
    """Static user overrides. ``fx_rate`` converts USD list prices. It is not fetched."""

    currency: str = "USD"
    fx_rate: Decimal = Decimal(1)
    aliases: dict[str, str] = field(default_factory=dict)
    prices: dict[str, ModelOverride] = field(default_factory=dict)
    discounts: dict[str, Decimal] = field(default_factory=dict)
    run_discount: Decimal | None = None
    origin: str | None = None
    fx_user_supplied: bool = False
    warnings: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.aliases or self.prices or self.discounts or self.run_discount is not None)

    def discount_for(self, provider: str, model: str) -> Decimal:
        return self.discount_for_models(provider, model)

    def discount_for_models(self, provider: str, *models: str) -> Decimal:
        if self.run_discount is not None:
            return self.run_discount
        for model in models:
            specific = self.discounts.get(f"{provider}/{model}")
            if specific is not None:
                return specific
            by_model = self.discounts.get(model)
            if by_model is not None:
                return by_model
        default = self.discounts.get("*")
        if default is not None:
            return default
        return Decimal(0)

    def with_run_discount(self, rate: Decimal | None) -> PricingConfig:
        if rate is None:
            return self
        return PricingConfig(
            currency=self.currency,
            fx_rate=self.fx_rate,
            aliases=self.aliases,
            prices=self.prices,
            discounts=self.discounts,
            run_discount=rate,
            origin=self.origin,
            fx_user_supplied=self.fx_user_supplied,
            warnings=self.warnings,
        )


def load_pricing_config(path: Path | None) -> PricingConfig:
    """Load ``path``, or ``./tracetally.toml`` when that file exists."""
    chosen = path
    if chosen is None:
        default = Path("tracetally.toml")
        if default.is_file():
            chosen = default
    if chosen is None:
        return PricingConfig()
    if not chosen.is_file():
        raise PriceError(f"pricing config not found: {chosen}")
    try:
        text = chosen.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PriceError(f"{chosen}: file is not valid UTF-8") from exc
    try:
        return parse_pricing_toml(text, origin=str(chosen))
    except PriceError:
        raise
    except ValueError as exc:
        raise PriceError(f"{chosen}: {exc}") from exc


_KNOWN_ROOT = frozenset({"currency", "fx_rate", "aliases", "discounts", "prices"})


def parse_pricing_toml(text: str, *, origin: str) -> PricingConfig:
    document = _parse_toml(text, origin)
    currency = "USD"
    fx_rate = Decimal(1)
    fx_user_supplied = "fx_rate" in document
    if "currency" in document:
        raw_currency = document["currency"]
        if not isinstance(raw_currency, str) or not raw_currency.strip():
            raise PriceError(f"{origin}: currency must be a string")
        currency = raw_currency.strip().upper()
    if "fx_rate" in document:
        fx_rate = _rate(document["fx_rate"], f"{origin}: fx_rate")
        if fx_rate <= 0:
            raise PriceError(f"{origin}: fx_rate must be greater than 0")
    aliases = _string_table(document.get("aliases"), f"{origin}: aliases")
    discounts = {
        key: _discount(value, f"{origin}: discounts.{key}")
        for key, value in _string_table(document.get("discounts"), f"{origin}: discounts").items()
    }
    prices = _prices(document.get("prices"), origin)
    unknown = sorted(set(document) - _KNOWN_ROOT)
    warnings = tuple(f"tracetally.toml: unknown key {key!r}" for key in unknown)
    return PricingConfig(
        currency=currency,
        fx_rate=fx_rate,
        aliases=aliases,
        prices=prices,
        discounts=discounts,
        origin=origin,
        fx_user_supplied=fx_user_supplied,
        warnings=warnings,
    )


def _prices(raw: object, origin: str) -> dict[str, ModelOverride]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PriceError(f"{origin}: prices must be a table")
    found: dict[str, ModelOverride] = {}
    for key, spec in raw.items():
        model = str(key).strip()
        if not model or not isinstance(spec, dict):
            raise PriceError(f"{origin}: prices.{key} must be a table")
        provider = spec.get("provider", "")
        if not isinstance(provider, str):
            raise PriceError(f"{origin}: prices.{model}.provider must be a string")
        found[model] = ModelOverride(
            provider=provider,
            input=_money_field(spec, "input", f"{origin}: prices.{model}"),
            output=_money_field(spec, "output", f"{origin}: prices.{model}"),
            cached=_optional_money(spec, "cached_input", f"{origin}: prices.{model}"),
            batch_input=_optional_money(spec, "batch_input", f"{origin}: prices.{model}"),
            batch_output=_optional_money(spec, "batch_output", f"{origin}: prices.{model}"),
            batch_cached=_optional_money(spec, "batch_cached_input", f"{origin}: prices.{model}"),
            priority_input=_optional_money(spec, "priority_input", f"{origin}: prices.{model}"),
            priority_output=_optional_money(spec, "priority_output", f"{origin}: prices.{model}"),
            priority_cached=_optional_money(
                spec, "priority_cached_input", f"{origin}: prices.{model}"
            ),
            flex_input=_optional_money(spec, "flex_input", f"{origin}: prices.{model}"),
            flex_output=_optional_money(spec, "flex_output", f"{origin}: prices.{model}"),
            flex_cached=_optional_money(spec, "flex_cached_input", f"{origin}: prices.{model}"),
            above_input=_optional_money(spec, "above_200k_input", f"{origin}: prices.{model}"),
            above_output=_optional_money(spec, "above_200k_output", f"{origin}: prices.{model}"),
            above_cached=_optional_money(
                spec, "above_200k_cached_input", f"{origin}: prices.{model}"
            ),
        )
    return found


def _money_field(spec: dict[str, object], key: str, where: str) -> Decimal:
    if key not in spec:
        raise PriceError(f"{where}: {key} is required (USD per 1M tokens)")
    return _non_negative(spec[key], f"{where}: {key}")


def _optional_money(spec: dict[str, object], key: str, where: str) -> Decimal | None:
    if key not in spec or spec[key] is None:
        return None
    return _non_negative(spec[key], f"{where}: {key}")


def _non_negative(value: object, where: str) -> Decimal:
    amount = _rate(value, where)
    if amount < 0:
        raise PriceError(f"{where} must be >= 0")
    return amount


def _discount(value: object, where: str) -> Decimal:
    amount = _rate(value, where)
    if amount < 0 or amount >= 1:
        raise PriceError(f"{where} must be a fraction in [0, 1)")
    return amount


def _rate(value: object, where: str) -> Decimal:
    try:
        return decimal_price(value)
    except ValueError as exc:
        raise PriceError(f"{where}: {exc}") from exc


def _string_table(raw: object, where: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PriceError(f"{where} must be a table")
    found: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name or not isinstance(value, str) or not value.strip():
            raise PriceError(f"{where}: {key} must be a string")
        found[name] = value.strip()
    return found


def _parse_toml(text: str, origin: str) -> dict[str, object]:
    """Subset parser: assignments, tables, dotted tables, quoted keys, comments."""
    root: dict[str, object] = {}
    current: dict[str, object] = root
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                raise PriceError(f"{origin}:{line_no}: table name must be [name]")
            current = _open_table(root, line[1:-1].strip(), f"{origin}:{line_no}")
            continue
        if "=" not in line:
            raise PriceError(f"{origin}:{line_no}: expected key = value")
        key, value = line.split("=", 1)
        name = _parse_key(key.strip(), f"{origin}:{line_no}")
        if not name:
            raise PriceError(f"{origin}:{line_no}: empty key")
        current[name] = _parse_value(value.strip(), f"{origin}:{line_no}")
    return root


def _open_table(root: dict[str, object], name: str, where: str) -> dict[str, object]:
    parts = _split_table(name, where)
    node: dict[str, object] = root
    for part in parts:
        found = node.get(part)
        if found is None:
            child: dict[str, object] = {}
            node[part] = child
            node = child
            continue
        if not isinstance(found, dict):
            raise PriceError(f"{where}: {part} is not a table")
        node = found
    return node


def _split_table(name: str, where: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    quoted = False
    i = 0
    while i < len(name):
        char = name[i]
        if char == '"':
            quoted = not quoted
            buf.append(char)
        elif char == "." and not quoted:
            parts.append(_parse_key("".join(buf).strip(), where))
            buf = []
        else:
            buf.append(char)
        i += 1
    if quoted:
        raise PriceError(f"{where}: unterminated quote in table name")
    parts.append(_parse_key("".join(buf).strip(), where))
    if not all(parts):
        raise PriceError(f"{where}: empty table name")
    return parts


def _parse_key(token: str, where: str) -> str:
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return _unquote(token, where)
    if token and all(ch.isalnum() or ch in {"_", "-"} for ch in token):
        return token
    raise PriceError(f"{where}: invalid key {token!r}")


def _parse_value(token: str, where: str) -> object:
    if not token:
        raise PriceError(f"{where}: missing value")
    if token[0] == '"':
        if len(token) < 2 or token[-1] != '"':
            raise PriceError(f"{where}: unterminated string")
        return _unquote(token, where)
    if token in {"true", "false"}:
        return token == "true"
    return token


def _unquote(token: str, where: str) -> str:
    body = token[1:-1]
    out: list[str] = []
    escaped = False
    for char in body:
        if escaped:
            mapping = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}
            if char not in mapping:
                raise PriceError(f"{where}: unknown escape \\{char}")
            out.append(mapping[char])
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        out.append(char)
    if escaped:
        raise PriceError(f"{where}: trailing escape")
    return "".join(out)


def _strip_comment(line: str) -> str:
    quoted = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
            continue
        if char == "#" and not quoted:
            return line[:index]
    return line
