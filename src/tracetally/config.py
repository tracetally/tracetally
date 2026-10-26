# SPDX-License-Identifier: Apache-2.0
"""Run configuration supplied by the CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

from tracetally.core.errors import UsageError


@dataclass(frozen=True)
class ReaderConfig:
    """Per-read options.

    Malformed lines and invalid records are skipped with a warning unless
    ``strict`` is set, in which case the first problem raises ``ReaderError``.
    """

    endpoint_attr: str | None = None
    strict: bool = False
    format_name: str | None = None


@dataclass(frozen=True)
class SummaryConfig:
    paths: tuple[Path, ...]
    reader: ReaderConfig = ReaderConfig()
    prices_path: Path | None = None
    json_output: bool = False


@dataclass(frozen=True)
class TierTarget:
    """Replacement model for one endpoint. Price only; not a quality claim."""

    model: str
    provider: str | None = None


@dataclass(frozen=True)
class CheckParams:
    """Defaults from the v0.1 design. A tracetally.yaml file can replace them."""

    bloat_min_calls: int = 20
    bloat_min_p50_input: int = 4000
    bloat_min_ratio: Decimal = Decimal(20)
    bloat_tail_multiple: Decimal = Decimal(3)
    bloat_adoption: tuple[Decimal, Decimal, Decimal] = (
        Decimal("0.25"),
        Decimal("0.50"),
        Decimal("0.75"),
    )
    cache_min_prefix_tokens: int = 1024
    cache_min_share: Decimal = Decimal("0.10")
    cache_hit_rates: tuple[Decimal, Decimal, Decimal] = (
        Decimal("0.50"),
        Decimal("0.75"),
        Decimal("0.90"),
    )
    batch_cluster_share: Decimal = Decimal("0.80")
    batch_eligible: tuple[Decimal, Decimal, Decimal] = (
        Decimal("0.50"),
        Decimal("0.75"),
        Decimal("1"),
    )
    retry_window_seconds: int = 60
    loop_min_repeats: int = 5
    latency_min_calls: int = 20
    latency_tail_multiple: Decimal = Decimal(5)
    rate_429_min: Decimal = Decimal("0.01")
    rate_5xx_min: Decimal = Decimal("0.005")


@dataclass(frozen=True)
class AnalyzeConfig:
    tier_map: dict[str, TierTarget] = field(default_factory=dict)
    params: CheckParams = field(default_factory=CheckParams)
    only: frozenset[str] | None = None
    skip: frozenset[str] = field(default_factory=frozenset)


def load_tier_map(path: Path) -> dict[str, TierTarget]:
    """Load ``endpoints: {name: {model, provider?}}``."""
    loaded = _load_yaml_mapping(path, what="tier map")
    raw = loaded.get("endpoints", loaded)
    if not isinstance(raw, dict) or not raw:
        raise UsageError(f"{path}: tier map needs an endpoints mapping")
    found: dict[str, TierTarget] = {}
    for name, spec in raw.items():
        endpoint = str(name).strip()
        if not endpoint:
            raise UsageError(f"{path}: endpoint name is empty")
        if not isinstance(spec, dict):
            raise UsageError(f"{path}: endpoint {endpoint!r} must be a mapping")
        model = spec.get("model")
        if not isinstance(model, str) or not model.strip():
            raise UsageError(f"{path}: endpoint {endpoint!r} needs a model")
        provider = spec.get("provider")
        if provider is not None and not isinstance(provider, str):
            raise UsageError(f"{path}: endpoint {endpoint!r} provider must be a string")
        found[endpoint] = TierTarget(
            model=model.strip(),
            provider=provider.strip() if isinstance(provider, str) and provider.strip() else None,
        )
    return found


def load_check_params(path: Path) -> CheckParams:
    """Overlay known thresholds from a YAML file. Unknown keys are an error."""
    loaded = _load_yaml_mapping(path, what="config")
    current = CheckParams()
    values: dict[str, object] = {
        "bloat_min_calls": current.bloat_min_calls,
        "bloat_min_p50_input": current.bloat_min_p50_input,
        "bloat_min_ratio": current.bloat_min_ratio,
        "bloat_tail_multiple": current.bloat_tail_multiple,
        "cache_min_prefix_tokens": current.cache_min_prefix_tokens,
        "cache_min_share": current.cache_min_share,
        "batch_cluster_share": current.batch_cluster_share,
        "retry_window_seconds": current.retry_window_seconds,
        "loop_min_repeats": current.loop_min_repeats,
        "latency_min_calls": current.latency_min_calls,
        "latency_tail_multiple": current.latency_tail_multiple,
        "rate_429_min": current.rate_429_min,
        "rate_5xx_min": current.rate_5xx_min,
    }
    sections = {
        "token_bloat": {
            "min_calls": "bloat_min_calls",
            "min_p50_input_tokens": "bloat_min_p50_input",
            "min_input_output_ratio": "bloat_min_ratio",
            "tail_multiple": "bloat_tail_multiple",
        },
        "cache_opportunity": {
            "min_prefix_tokens": "cache_min_prefix_tokens",
            "min_call_share": "cache_min_share",
        },
        "batch_candidates": {"cluster_share": "batch_cluster_share"},
        "retry_waste": {"window_seconds": "retry_window_seconds"},
        "agent_loops": {"min_repeats": "loop_min_repeats"},
        "latency_tail": {
            "min_calls": "latency_min_calls",
            "tail_multiple": "latency_tail_multiple",
        },
        "error_rates": {"rate_429_min": "rate_429_min", "rate_5xx_min": "rate_5xx_min"},
    }
    unknown_sections = sorted(set(loaded) - set(sections))
    if unknown_sections:
        known = ", ".join(sorted(sections))
        raise UsageError(f"{path}: unknown config key {unknown_sections[0]!r} (known: {known})")
    for section, mapping in sections.items():
        block = loaded.get(section)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise UsageError(f"{path}: {section} must be a mapping")
        for key, raw in block.items():
            target = mapping.get(str(key))
            if target is None:
                known = ", ".join(sorted(mapping))
                raise UsageError(f"{path}: unknown {section}.{key} (known: {known})")
            values[target] = _as_param(raw, f"{path}: {section}.{key}")
    return CheckParams(
        bloat_min_calls=_as_int(values["bloat_min_calls"], "bloat_min_calls"),
        bloat_min_p50_input=_as_int(values["bloat_min_p50_input"], "bloat_min_p50_input"),
        bloat_min_ratio=_as_decimal(values["bloat_min_ratio"]),
        bloat_tail_multiple=_as_decimal(values["bloat_tail_multiple"]),
        cache_min_prefix_tokens=_as_int(
            values["cache_min_prefix_tokens"], "cache_min_prefix_tokens"
        ),
        cache_min_share=_as_decimal(values["cache_min_share"]),
        batch_cluster_share=_as_decimal(values["batch_cluster_share"]),
        retry_window_seconds=_as_int(values["retry_window_seconds"], "retry_window_seconds"),
        loop_min_repeats=_as_int(values["loop_min_repeats"], "loop_min_repeats"),
        latency_min_calls=_as_int(values["latency_min_calls"], "latency_min_calls"),
        latency_tail_multiple=_as_decimal(values["latency_tail_multiple"]),
        rate_429_min=_as_decimal(values["rate_429_min"]),
        rate_5xx_min=_as_decimal(values["rate_5xx_min"]),
    )


def _load_yaml_mapping(path: Path, *, what: str) -> dict[str, object]:
    if not path.is_file():
        raise UsageError(f"{what} file not found: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise UsageError(f"{path}: file is not valid UTF-8") from exc
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise UsageError(f"{path}: invalid YAML ({exc})") from exc
    if not isinstance(loaded, dict):
        raise UsageError(f"{path}: {what} must be a mapping")
    return loaded


def _as_param(value: object, where: str) -> object:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise UsageError(f"{where}: expected a number")
    return value


def _as_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise UsageError(f"{where}: expected an integer")
    try:
        number = int(str(value))
    except ValueError as exc:
        raise UsageError(f"{where}: expected an integer") from exc
    if number < 0:
        raise UsageError(f"{where}: must be >= 0")
    return number


def _as_decimal(value: object) -> Decimal:
    return Decimal(str(value))
