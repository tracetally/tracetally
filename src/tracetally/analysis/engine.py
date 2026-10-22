# SPDX-License-Identifier: Apache-2.0
"""Run checks whose reader capabilities are present. Rank the findings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from io import StringIO

from rich import box
from rich.console import Console
from rich.table import Table

from tracetally import __version__
from tracetally.analysis.checks import CHECKS, Check
from tracetally.analysis.checks.common import monthly_factor
from tracetally.analysis.summary import METHODOLOGY, Summary, summarize
from tracetally.config import AnalyzeConfig
from tracetally.core.errors import UsageError
from tracetally.core.money import format_usd
from tracetally.core.schema import Capability, Finding, Level, SkippedCheck
from tracetally.core.store import TraceStore
from tracetally.pricing.table import PriceTable

ANALYZE_SCHEMA_VERSION = 1

_CONFIDENCE_RANK = {Level.HIGH: 3, Level.MED: 2, Level.LOW: 1}
_RELIABILITY = frozenset({"latency_tail", "error_rates", "observability_gap"})

_ANALYZE_METHOD = (
    "Monthly dollars are observed dollars × (30 / window days), with a 1-day floor. "
    "Token bloat uses adoption 25/50/75%. Cache opportunity uses hit rates 50/75/90% "
    "on tokens that are not already cached. Batch candidates use 50/75/100% of calls "
    "movable, and the batch price follows the price file's batch_cached rule rather "
    "than halving every token. Retry waste and agent loops are observed spend, so "
    "low, mid, and high match. Model swap is a what-if: price only, validate quality "
    "with evals. It is not added to the savings total."
)


@dataclass
class Analysis:
    summary: Summary
    findings: list[Finding]
    skipped: list[SkippedCheck]
    monthly_factor: Decimal
    monthly_caveats: list[str]

    def savings(self) -> list[Finding]:
        return [
            item
            for item in self.findings
            if item.check not in _RELIABILITY and not item.what_if and not _zero(item)
        ]

    def reliability(self) -> list[Finding]:
        return [item for item in self.findings if item.check in _RELIABILITY or _zero(item)]

    def to_dict(self) -> dict[str, object]:
        savings = [
            item for item in self.findings if not item.what_if and item.check not in _RELIABILITY
        ]
        what_ifs = [item for item in self.findings if item.what_if]
        return {
            "analyze_schema_version": ANALYZE_SCHEMA_VERSION,
            "tracetally_version": __version__,
            "price_table_id": self.summary.price_table_id,
            "price_table_retrieved_on": self.summary.price_table_retrieved_on,
            "currency": self.summary.currency,
            "methodology": METHODOLOGY + " " + _ANALYZE_METHOD,
            "monthly": {
                "factor": _money(self.monthly_factor),
                "window_start": _iso(self.summary.time_start),
                "window_end": _iso(self.summary.time_end),
                "caveats": list(self.monthly_caveats),
            },
            "totals": {
                "calls": self.summary.calls,
                "priced_calls": self.summary.priced_calls,
                "observed_cost_usd": _money(self.summary.cost_usd),
                "est_monthly_savings_usd": _range_sum(savings),
                "est_monthly_what_if_usd": _range_sum(what_ifs),
            },
            "findings": [_finding_dict(item) for item in self.findings],
            "skipped_checks": [
                {"check": item.check, "reason": item.reason} for item in self.skipped
            ],
            "warnings": list(self.summary.warnings),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2) + "\n"

    def to_text(self) -> str:
        buffer = StringIO()
        console = Console(
            file=buffer,
            width=168,
            force_terminal=False,
            color_system=None,
            legacy_windows=False,
        )
        summary = self.summary
        console.print(
            f"tracetally {__version__} analyze  "
            f"price table {summary.price_table_id} (retrieved {summary.price_table_retrieved_on})"
        )
        console.print(
            f"calls {summary.calls}  priced {summary.priced_calls}/{summary.calls}  "
            f"observed cost {format_usd(summary.cost_usd)} {summary.currency}  "
            f"monthly factor {_money(self.monthly_factor)}"
        )
        window = f"{_iso(summary.time_start) or '-'} to {_iso(summary.time_end) or '-'}"
        console.print(f"window {window}")
        savings_sum = _range_sum(
            [item for item in self.findings if not item.what_if and item.check not in _RELIABILITY]
        )
        what_if_sum = _range_sum([item for item in self.findings if item.what_if])
        console.print(
            "est. monthly savings "
            f"{savings_sum['low']} / {savings_sum['mid']} / {savings_sum['high']}  "
            "what-if "
            f"{what_if_sum['low']} / {what_if_sum['mid']} / {what_if_sum['high']}"
        )
        console.print()
        dollar_findings = [item for item in self.findings if not _zero(item)]
        other = [item for item in self.findings if _zero(item)]
        if dollar_findings:
            console.print(_findings_table("Findings", dollar_findings))
            console.print()
            for item in dollar_findings:
                _print_finding(console, item)
        else:
            console.print("No dollar findings.")
            console.print()
        if other:
            console.print(_findings_table("Reliability and coverage", other))
            console.print()
            for item in other:
                _print_finding(console, item)
        if self.skipped:
            console.print("Skipped checks")
            for skipped in self.skipped:
                console.print(f"- {skipped.check}: {skipped.reason}")
            console.print()
        if self.summary.warnings:
            console.print("Warnings")
            for warning in self.summary.warnings:
                console.print(f"- {warning}")
            console.print()
        console.print(METHODOLOGY)
        console.print(_ANALYZE_METHOD)
        console.print("Offline. No network calls. No telemetry.")
        return buffer.getvalue()


def analyze(store: TraceStore, table: PriceTable, cfg: AnalyzeConfig | None = None) -> Analysis:
    selected = cfg or AnalyzeConfig()
    _reject_unknown(selected)
    summary = summarize(store, table)
    factor, caveats = monthly_factor(store)
    caps: frozenset[Capability] = (
        store.capabilities if store.capabilities is not None else frozenset()
    )
    findings: list[Finding] = []
    skipped: list[SkippedCheck] = []
    for check in CHECKS:
        reason = _skip_reason(check, selected, caps)
        if reason is not None:
            skipped.append(SkippedCheck(check=check.id, reason=reason))
            continue
        findings.extend(check.run(store, table, selected))
    findings.sort(key=_rank)
    return Analysis(
        summary=summary,
        findings=findings,
        skipped=skipped,
        monthly_factor=factor,
        monthly_caveats=caveats,
    )


def _reject_unknown(cfg: AnalyzeConfig) -> None:
    known = {check.id for check in CHECKS}
    named = set(cfg.skip)
    if cfg.only is not None:
        named |= set(cfg.only)
    unknown = sorted(named - known)
    if unknown:
        raise UsageError(f"unknown check {unknown[0]!r}; known checks: {', '.join(sorted(known))}")


def _skip_reason(check: Check, cfg: AnalyzeConfig, caps: frozenset[Capability]) -> str | None:
    if cfg.only is not None and check.id not in cfg.only:
        return "not selected by --only"
    if check.id in cfg.skip:
        return "skipped with --skip"
    missing = check.requires - caps
    if missing:
        names = ", ".join(sorted(cap.value for cap in missing))
        return f"reader is missing capabilities: {names}"
    if check.id == "model_swap" and not cfg.tier_map:
        return (
            "pass --tier-map to reprice endpoints; model swap is a what-if and is skipped "
            "without a target model"
        )
    return None


def _rank(item: Finding) -> tuple[int, Decimal, int, str]:
    section = 1 if item.check in _RELIABILITY or _zero(item) else 0
    return (
        section,
        -item.est_monthly_usd.mid,
        -_CONFIDENCE_RANK.get(item.confidence, 0),
        item.id,
    )


def _zero(item: Finding) -> bool:
    return item.est_monthly_usd.mid == 0 and item.est_monthly_usd.high == 0


def _findings_table(title: str, findings: list[Finding]) -> Table:
    table = Table(title=title, box=box.ASCII, show_lines=False)
    for column in (
        "Check",
        "Endpoint",
        "Low",
        "Mid",
        "High",
        "Effort",
        "Risk",
        "Confidence",
        "What-if",
    ):
        table.add_column(column, overflow="fold")
    for item in findings:
        endpoint = item.evidence[0].endpoint if item.evidence else ""
        table.add_row(
            item.check,
            endpoint or "",
            format_usd(item.est_monthly_usd.low),
            format_usd(item.est_monthly_usd.mid),
            format_usd(item.est_monthly_usd.high),
            item.effort.value,
            item.quality_risk.value,
            item.confidence.value,
            "what-if" if item.what_if else "",
        )
    return table


def _print_finding(console: Console, item: Finding) -> None:
    label = " what-if" if item.what_if else ""
    console.print(f"{item.id}{label}")
    console.print(f"  {item.title}")
    console.print(f"  {item.recommendation}")
    for row in item.evidence:
        if row.detail:
            console.print(f"  evidence: {row.detail}")
    for caveat in item.caveats:
        console.print(f"  caveat: {caveat}")
    console.print()


def _finding_dict(item: Finding) -> dict[str, object]:
    return {
        "id": item.id,
        "check": item.check,
        "title": item.title,
        "severity": item.severity.value,
        "est_monthly_usd": {
            "low": _money(item.est_monthly_usd.low),
            "mid": _money(item.est_monthly_usd.mid),
            "high": _money(item.est_monthly_usd.high),
        },
        "effort": item.effort.value,
        "quality_risk": item.quality_risk.value,
        "confidence": item.confidence.value,
        "what_if": item.what_if,
        "recommendation": item.recommendation,
        "caveats": list(item.caveats),
        "evidence": [
            {
                "trace_ids": list(row.trace_ids),
                "count": row.count,
                "endpoint": row.endpoint,
                "detail": row.detail,
            }
            for row in item.evidence
        ],
    }


def _range_sum(findings: list[Finding]) -> dict[str, str]:
    low = Decimal(0)
    mid = Decimal(0)
    high = Decimal(0)
    for item in findings:
        low += item.est_monthly_usd.low
        mid += item.est_monthly_usd.mid
        high += item.est_monthly_usd.high
    return {"low": _money(low) or "0", "mid": _money(mid) or "0", "high": _money(high) or "0"}


def _money(amount: Decimal | None) -> str | None:
    if amount is None:
        return None
    text = format(amount.quantize(Decimal("0.000000000001")), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
