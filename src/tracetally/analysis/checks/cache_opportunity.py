# SPDX-License-Identifier: Apache-2.0
"""Repeated prompt prefixes that are not already billed as cache hits."""

from __future__ import annotations

from decimal import Decimal

from tracetally.analysis.checks.common import (
    cache_delta,
    evidence,
    make_finding,
    money_for_tokens,
    monthly_factor,
)
from tracetally.config import AnalyzeConfig
from tracetally.core.schema import Capability, Effort, Finding, Level
from tracetally.core.store import TraceStore
from tracetally.pricing.table import PriceTable

REQUIRES = frozenset({Capability.PROMPT_HASHES})


def cache_opportunity(store: TraceStore, table: PriceTable, cfg: AnalyzeConfig) -> list[Finding]:
    params = cfg.params
    factor, window_caveats = monthly_factor(store)
    findings: list[Finding] = []
    for endpoint, indexes in sorted(store.indexes().by_endpoint.items()):
        findings.extend(
            _endpoint_findings(
                store,
                table,
                endpoint,
                indexes,
                min_tokens=params.cache_min_prefix_tokens,
                min_share=params.cache_min_share,
                hit_rates=params.cache_hit_rates,
                factor=factor,
                window_caveats=window_caveats,
            )
        )
    return findings


def _endpoint_findings(
    store: TraceStore,
    table: PriceTable,
    endpoint: str,
    indexes: list[int],
    *,
    min_tokens: int,
    min_share: Decimal,
    hit_rates: tuple[Decimal, Decimal, Decimal],
    factor: Decimal,
    window_caveats: list[str],
) -> list[Finding]:
    n = len(indexes)
    if n == 0:
        return []
    groups: dict[tuple[int, str], list[int]] = {}
    for index in indexes:
        for tokens, digest in store.prefix_hashes[index]:
            if tokens < min_tokens or not digest:
                continue
            groups.setdefault((tokens, digest), []).append(index)
    claimed: set[int] = set()
    findings: list[Finding] = []
    for (tokens, digest), members in sorted(
        groups.items(), key=lambda item: (-item[0][0], item[0][1])
    ):
        fresh = [index for index in members if index not in claimed]
        if not fresh:
            continue
        share = Decimal(len(fresh)) / Decimal(n)
        if share < min_share:
            continue
        claimed.update(fresh)
        finding = _price_group(
            store,
            table,
            endpoint=endpoint,
            indexes=fresh,
            endpoint_calls=n,
            boundary=tokens,
            digest=digest,
            share=share,
            hit_rates=hit_rates,
            factor=factor,
            window_caveats=window_caveats,
        )
        if finding is not None:
            findings.append(finding)
    return findings


def _price_group(
    store: TraceStore,
    table: PriceTable,
    *,
    endpoint: str,
    indexes: list[int],
    endpoint_calls: int,
    boundary: int,
    digest: str,
    share: Decimal,
    hit_rates: tuple[Decimal, Decimal, Decimal],
    factor: Decimal,
    window_caveats: list[str],
) -> Finding | None:
    hashes = {store.content_hash[index] for index in indexes}
    full_repeat = len(hashes) == 1 and next(iter(hashes)) not in (None, "")
    base = Decimal(0)
    repeated_tokens = 0
    cached_tokens = 0
    unpriced = 0
    no_cache_price = 0
    for index in indexes:
        price = table.resolve(store.provider[index], store.model[index])
        reported = int(store.input_tokens[index])
        prefix_tokens = reported if full_repeat else min(boundary, reported)
        repeated_tokens += prefix_tokens
        cached = min(int(store.cached_input_tokens[index]), prefix_tokens)
        cached_tokens += cached
        if price is None:
            unpriced += 1
            continue
        delta = cache_delta(price, reported)
        if delta is None:
            no_cache_price += 1
            continue
        base += money_for_tokens(Decimal(prefix_tokens - cached), delta)
    if base <= 0:
        return None
    low, mid, high = hit_rates
    observed = (
        Decimal(0) if repeated_tokens == 0 else Decimal(cached_tokens) / Decimal(repeated_tokens)
    )
    if full_repeat:
        length_note = (
            f"the full input repeats ({repeated_tokens} reported tokens across "
            f"{len(indexes)} calls); the longest hash boundary reached is {boundary}"
        )
    else:
        length_note = (
            f"shared prefix bucket is {boundary} tokens "
            f"({repeated_tokens} tokens across {len(indexes)} calls). "
            "Prefixes that fall between hash boundaries are priced at the lower boundary"
        )
    caveats = [
        "Savings are (input price − cached-input price) × uncached repeated prefix tokens "
        "× an assumed hit rate of 50/75/90%. The hit rate is not a measured future rate. "
        "Prefix hashes use a chars/4 estimate only to choose the bucket; the dollars use "
        "reported token counts.",
        *window_caveats,
    ]
    if unpriced:
        caveats.append(f"{unpriced} calls use an unpriced model and are omitted from the dollars.")
    if no_cache_price:
        caveats.append(
            f"{no_cache_price} calls have no published cached-input price, so no cache "
            "discount was assumed for them."
        )
    return make_finding(
        check="cache_opportunity",
        endpoint=endpoint,
        title=f"Repeated prefix on {endpoint} is mostly uncached",
        low=base * low,
        mid=base * mid,
        high=base * high,
        factor=factor,
        effort=Effort.S,
        quality_risk=Level.LOW,
        confidence=Level.HIGH if full_repeat else Level.MED,
        recommendation=(
            f"Keep the stable prefix of {endpoint} identical and above the provider cache "
            "minimum, and put dynamic text after it. Confirm cache hits in the provider "
            "usage fields before counting this as saved spend."
        ),
        caveats=caveats,
        evidence_rows=[
            evidence(
                store,
                indexes,
                endpoint=endpoint,
                detail=(
                    f"{len(indexes)}/{endpoint_calls} calls ({share:.2%}) share prefix "
                    f"{digest[:12]}. {length_note}. Observed cached-token share of that "
                    f"prefix is {observed:.2%}."
                ),
            )
        ],
    )
