# SPDX-License-Identifier: Apache-2.0
"""Write report.md, report.html, and findings.json. Nothing is fetched."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from tracetally import __version__
from tracetally.analysis.engine import ANALYZE_METHODOLOGY, RELIABILITY_CHECKS, Analysis
from tracetally.analysis.summary import METHODOLOGY, GroupSummary, Summary
from tracetally.core.errors import UsageError
from tracetally.core.hashing import CONTENT_RETAIN_CHARS
from tracetally.core.money import format_usd, money_text
from tracetally.core.schema import Finding, InputDigest, RunResult
from tracetally.core.stats import rate
from tracetally.core.store import TraceStore
from tracetally.report.links import EVALS_URL, PROJECT_URL
from tracetally.report.redact import redact_analysis
from tracetally.report.svg import bar_chart, empty_chart, latency_chart

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
EXCERPT_CHARS = 500
EXCERPTS_PER_FINDING = 2
_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReportOptions:
    """How one report is labelled and written."""

    out: Path
    redact: bool = False
    include_content: bool = False
    content_requested: bool = False
    footer: bool = True
    synthetic: bool = False
    demo_seed: int | None = None
    generated_at: datetime | None = None
    redact_salt: str | None = None


@dataclass(frozen=True)
class WrittenReport:
    markdown: Path
    html: Path
    findings: Path
    analysis: Analysis


def write_report(store: TraceStore, analysis: Analysis, options: ReportOptions) -> WrittenReport:
    """Render the three report files. Raw prompt text is included only on request."""
    out = _prepare_dir(options.out)
    content_in_report = options.include_content and not options.redact
    excerpts = _excerpt_map(store, analysis) if content_in_report else {}
    shown = analysis
    if options.redact:
        salt = options.redact_salt or secrets.token_hex(16)
        shown = redact_analysis(analysis, store, salt)
    context = _context(shown, options, excerpts, content_in_report=content_in_report)
    html = _environment(autoescape=True).get_template("report.html.j2").render(context)
    markdown = _environment(autoescape=False).get_template("report.md.j2").render(context)
    document = _document(shown, options, excerpts, content_in_report=content_in_report)
    md_path = out / "report.md"
    html_path = out / "report.html"
    json_path = out / "findings.json"
    _write(md_path, markdown if markdown.endswith("\n") else markdown + "\n")
    _write(html_path, html if html.endswith("\n") else html + "\n")
    _write(json_path, json.dumps(document, indent=2) + "\n")
    return WrittenReport(markdown=md_path, html=html_path, findings=json_path, analysis=shown)


def _prepare_dir(path: Path) -> Path:
    if path.exists() and not path.is_dir():
        raise UsageError(f"--out exists and is not a directory: {path}")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UsageError(f"could not create {path}: {exc}") from exc
    return path


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _environment(*, autoescape: bool) -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=autoescape,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["md_cell"] = _md_cell
    return env


def _md_cell(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _context(
    analysis: Analysis,
    options: ReportOptions,
    excerpts: dict[str, list[dict[str, str]]],
    *,
    content_in_report: bool,
) -> dict[str, Any]:
    summary = analysis.summary
    savings, what_ifs = _split_totals(analysis.findings)
    cards = [
        _card(item, rank, excerpts) for rank, item in enumerate(_dollar(analysis.findings), start=1)
    ]
    reliability = [
        _card(item, index, excerpts)
        for index, item in enumerate(_reliability(analysis.findings), start=1)
    ]
    observed = summary.cost_usd
    monthly = None if observed is None else observed * analysis.monthly_factor
    return {
        "version": __version__,
        "generated_at": _generated_at(options),
        "synthetic": options.synthetic,
        "demo_seed": options.demo_seed,
        "redacted": options.redact,
        "include_content": content_in_report,
        "footer": options.footer,
        "project_url": PROJECT_URL,
        "evals_url": EVALS_URL,
        "price_table_id": summary.price_table_id,
        "price_retrieved_on": summary.price_table_retrieved_on,
        "price_source": summary.price_source,
        "price_url": summary.price_url,
        "price_sha256": summary.price_sha256,
        "priced_share": summary.priced_share,
        "show_discount": summary.show_discount,
        "list_observed": format_usd(summary.list_cost_usd),
        "currency": summary.currency,
        "fx_rate": summary.fx_rate,
        "fx_user_supplied": summary.fx_user_supplied,
        "billed_cost": format_usd(summary.billed_cost_usd),
        "inferred_cost": format_usd(summary.inferred_cost_usd),
        "cost_gap": summary.cost_gap,
        "mode": summary.mode,
        "cost_only": summary.mode == "cost-only",
        "window_start": _iso(summary.time_start),
        "window_end": _iso(summary.time_end),
        "monthly_factor": money_text(analysis.monthly_factor) or "1",
        "monthly_caveats": list(analysis.monthly_caveats),
        "calls": summary.calls,
        "requests": summary.requests,
        "priced_calls": summary.priced_calls,
        "unpriced_calls": summary.unpriced_calls,
        "observed_cost": format_usd(observed),
        "monthly_spend": format_usd(monthly),
        "savings_low": format_usd(savings[0]),
        "savings_mid": format_usd(savings[1]),
        "savings_high": format_usd(savings[2]),
        "what_if_low": format_usd(what_ifs[0]),
        "what_if_mid": format_usd(what_ifs[1]),
        "what_if_high": format_usd(what_ifs[2]),
        "top_findings": cards[:3],
        "findings": cards,
        "reliability": reliability,
        "skipped": [{"check": item.check, "reason": item.reason} for item in analysis.skipped],
        "warnings": list(summary.warnings),
        "model_rows": [_model_row(group, summary.latency_measured) for group in summary.by_model],
        "endpoint_rows": [
            _endpoint_row(group, summary.latency_measured) for group in summary.by_endpoint_model
        ],
        "error_text": _error_text(summary),
        "inputs": list(summary.inputs),
        "methodology": _methodology(analysis, options, content_in_report=content_in_report),
        "charts": {
            "cost_by_model": Markup(_cost_by_model(summary)),
            "cost_by_endpoint": Markup(_cost_by_endpoint(summary)),
            "latency": Markup(_latency(summary)),
        },
        "style": Markup(_css()),
        "schema_version": _SCHEMA_VERSION,
    }


def _document(
    analysis: Analysis,
    options: ReportOptions,
    excerpts: dict[str, list[dict[str, str]]],
    *,
    content_in_report: bool,
) -> dict[str, Any]:
    summary = analysis.summary
    savings, what_ifs = _split_totals(analysis.findings)
    observed = summary.cost_usd
    monthly = None if observed is None else observed * analysis.monthly_factor
    flat: list[dict[str, str]] = []
    for finding in analysis.findings:
        for row in excerpts.get(finding.id, []):
            flat.append(
                {"finding_id": finding.id, "trace_id": row["trace_id"], "text": row["text"]}
            )
    result = RunResult(
        tracetally_version=__version__,
        generated_at=_generated_at(options),
        synthetic=options.synthetic,
        demo_seed=options.demo_seed,
        redacted=options.redact,
        include_content=content_in_report,
        show_footer=options.footer,
        price_table_id=summary.price_table_id,
        price_table_retrieved_on=summary.price_table_retrieved_on,
        price_source=summary.price_source,
        price_url=summary.price_url,
        price_sha256=summary.price_sha256,
        priced_share=summary.priced_share,
        currency=summary.currency,
        fx_rate=summary.fx_rate,
        methodology="\n\n".join(
            _methodology(analysis, options, content_in_report=content_in_report)
        ),
        monthly_factor=money_text(analysis.monthly_factor) or "1",
        monthly_caveats=list(analysis.monthly_caveats),
        totals={
            "calls": summary.calls,
            "priced_calls": summary.priced_calls,
            "unpriced_calls": summary.unpriced_calls,
            "priced_share": summary.priced_share,
            "observed_cost_usd": money_text(observed),
            "billed_cost_usd": money_text(summary.billed_cost_usd),
            "inferred_cost_usd": money_text(summary.inferred_cost_usd),
            "cost_gap": summary.cost_gap,
            "fx_rate": summary.fx_rate,
            "fx_user_supplied": summary.fx_user_supplied,
            "list_cost_usd": money_text(summary.list_cost_usd),
            "est_monthly_spend_usd": money_text(monthly),
            "est_monthly_savings_usd": _range_json(savings),
            "est_monthly_what_if_usd": _range_json(what_ifs),
        },
        summary=summary.to_dict(),
        findings=list(analysis.findings),
        skipped_checks=list(analysis.skipped),
        inputs=[_input_digest(item) for item in summary.inputs],
        warnings=list(summary.warnings),
        content_excerpts=flat,
    )
    return result.model_dump(mode="json")


def _input_digest(item: dict[str, object]) -> InputDigest:
    return InputDigest(
        path=str(item.get("path", "")),
        sha256=str(item.get("sha256", "")),
        records=_as_int(item.get("records")),
        skipped=_as_int(item.get("skipped")),
        spans=_as_int(item.get("spans")),
    )


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _excerpt_map(store: TraceStore, analysis: Analysis) -> dict[str, list[dict[str, str]]]:
    by_trace: dict[str, str] = {}
    for index, trace_id in enumerate(store.trace_id):
        if not trace_id or trace_id in by_trace:
            continue
        text = store.content[index]
        if text:
            by_trace[trace_id] = text
    found: dict[str, list[dict[str, str]]] = {}
    for finding in analysis.findings:
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for evidence in finding.evidence:
            for trace_id in evidence.trace_ids:
                if not trace_id or trace_id in seen:
                    continue
                text = by_trace.get(trace_id)
                if not text:
                    continue
                seen.add(trace_id)
                shown = text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS] + "…"
                rows.append({"trace_id": trace_id, "text": shown})
                if len(rows) >= EXCERPTS_PER_FINDING:
                    break
            if len(rows) >= EXCERPTS_PER_FINDING:
                break
        if rows:
            found[finding.id] = rows
    return found


def _card(item: Finding, rank: int, excerpts: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    amounts = item.est_monthly_usd
    has_dollars = not (amounts.mid == 0 and amounts.high == 0)
    return {
        "rank": rank,
        "id": item.id,
        "check": item.check,
        "title": item.title,
        "severity": item.severity.value,
        "effort": item.effort.value,
        "quality_risk": item.quality_risk.value,
        "confidence": item.confidence.value,
        "what_if": item.what_if,
        "has_dollars": has_dollars,
        "low": format_usd(amounts.low),
        "mid": format_usd(amounts.mid),
        "high": format_usd(amounts.high),
        "recommendation": item.recommendation,
        "caveats": list(item.caveats),
        "evidence": [_evidence(row) for row in item.evidence],
        "excerpts": excerpts.get(item.id, []),
    }


def _evidence(row: Any) -> dict[str, str]:
    traces = ", ".join(trace_id for trace_id in row.trace_ids if trace_id)
    return {
        "endpoint": row.endpoint or "—",
        "count": "—" if row.count is None else str(row.count),
        "traces": traces or "—",
        "detail": row.detail or "",
    }


def _dollar(findings: list[Finding]) -> list[Finding]:
    return [item for item in findings if not _reliability_item(item)]


def _reliability(findings: list[Finding]) -> list[Finding]:
    return [item for item in findings if _reliability_item(item)]


def _reliability_item(item: Finding) -> bool:
    amounts = item.est_monthly_usd
    zero = amounts.mid == 0 and amounts.high == 0
    return item.check in RELIABILITY_CHECKS or zero


def _split_totals(
    findings: list[Finding],
) -> tuple[tuple[Decimal, Decimal, Decimal], tuple[Decimal, Decimal, Decimal]]:
    savings = [
        item for item in findings if not item.what_if and item.check not in RELIABILITY_CHECKS
    ]
    what_ifs = [item for item in findings if item.what_if]
    return _sum(savings), _sum(what_ifs)


def _sum(findings: list[Finding]) -> tuple[Decimal, Decimal, Decimal]:
    low = mid = high = Decimal(0)
    for item in findings:
        low += item.est_monthly_usd.low
        mid += item.est_monthly_usd.mid
        high += item.est_monthly_usd.high
    return low, mid, high


def _range_json(amounts: tuple[Decimal, Decimal, Decimal]) -> dict[str, str]:
    return {
        "low": money_text(amounts[0]) or "0",
        "mid": money_text(amounts[1]) or "0",
        "high": money_text(amounts[2]) or "0",
    }


def _labelled(group: GroupSummary) -> str:
    if group.price_label:
        return f"{group.model} ({group.price_label})"
    return group.model


def _report_cost(group: GroupSummary) -> str:
    if group.list_cost_usd is not None and group.cost_usd is not None:
        return f"{format_usd(group.list_cost_usd)} list / {format_usd(group.cost_usd)} effective"
    return format_usd(group.cost_usd)


def _model_row(group: GroupSummary, measured: bool) -> dict[str, str]:
    latency = group.latency()
    return {
        "provider": group.provider,
        "model": _labelled(group),
        "requests": str(group.requests),
        "input_tokens": str(group.input_tokens),
        "cached_input_tokens": str(group.cached_input_tokens),
        "output_tokens": str(group.output_tokens),
        "cost": _report_cost(group),
        "n": str(latency.n),
        "p50": _lat(latency.p50, latency.hidden, measured),
        "p95": _lat(latency.p95, latency.hidden, measured),
        "p99": _lat(latency.p99, latency.hidden, measured),
        "n_429": str(group.n_429),
        "n_5xx": str(group.n_5xx),
    }


def _endpoint_row(group: GroupSummary, measured: bool) -> dict[str, str]:
    row = _model_row(group, measured)
    row["endpoint"] = group.endpoint or "(unknown)"
    return row


def _lat(value: float | None, hidden: bool, measured: bool) -> str:
    if not measured:
        return "n/a"
    if hidden or value is None:
        return "n<20"
    return f"{value:.3f}"


def _error_text(summary: Summary) -> str:
    if not summary.errors_measured:
        return "Error rates were not measured. This export has no status or error type."

    def one(count: int) -> str:
        value = rate(count, summary.requests)
        shown = "-" if value is None else f"{value:.6f}"
        return f"{shown} ({count}/{summary.requests})"

    return f"429 {one(summary.n_429)}. 5xx {one(summary.n_5xx)}. Any error {one(summary.n_error)}."


def _methodology(
    analysis: Analysis, options: ReportOptions, *, content_in_report: bool
) -> list[str]:
    summary = analysis.summary
    paragraphs = [
        METHODOLOGY,
        ANALYZE_METHODOLOGY,
        (
            f"Prices {summary.price_source} from {summary.price_url}, "
            f"retrieved {summary.price_table_retrieved_on}, sha256 {summary.price_sha256}. "
            f"Currency {summary.currency}. Monthly factor {money_text(analysis.monthly_factor)} "
            "(observed dollars times 30 / window days, with a 1-day floor)."
        ),
        _content_note(options, content_in_report=content_in_report),
        (
            "Effort is S, M, or L. Quality risk and confidence are low, med, or high. "
            "Findings are ranked by estimated monthly dollars (mid), then confidence. "
            "Reliability findings with no dollars are in their own section."
        ),
        "No telemetry. The only network request tracetally makes is the LiteLLM price file, "
        "and this run records which copy it used. "
        "tracetally is not a proxy, gateway, SDK, or server.",
    ]
    if options.synthetic:
        seed = "" if options.demo_seed is None else f" Seed {options.demo_seed}."
        paragraphs.append(
            "SYNTHETIC DATA. The Acme Support Bot rows are a fixed planted sample, "
            "not a customer export."
            + seed
            + " The seed labels the run and does not redraw the rows."
        )
    return paragraphs


def _content_note(options: ReportOptions, *, content_in_report: bool) -> str:
    if options.redact and options.content_requested:
        return (
            "--include-content was set together with --redact. Raw prompt text was not kept. "
            "Trace ids, user ids, session ids, and input paths were hashed with a per-run salt "
            "that is not written into this report. The file SHA-256 is kept so you can match "
            "the run to a file you still hold."
        )
    if options.redact:
        return (
            "Trace ids, user ids, session ids, and input paths were hashed with a per-run salt "
            "that is not written into this report. Prompt text is hashed at read and is not in "
            "this report. The file SHA-256 is kept."
        )
    if content_in_report:
        return (
            "Raw prompt text is included because --include-content was set. "
            f"Each call keeps at most {CONTENT_RETAIN_CHARS} characters, and each finding shows "
            f"at most {EXCERPTS_PER_FINDING} excerpts of {EXCERPT_CHARS} characters from its "
            "evidence traces. This is not a full prompt export."
        )
    return (
        "Prompt and response text is hashed at read and is not in this report. "
        "The report has aggregates, evidence ids, and prefix hashes."
    )


def _iso(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.isoformat()


def _generated_at(options: ReportOptions) -> str:
    when = options.generated_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _css() -> str:
    text = (TEMPLATE_DIR / "_style.css").read_text(encoding="utf-8")
    lowered = text.lower()
    if any(token in lowered for token in ("</style", "url(", "@import", "http://", "https://")):
        raise UsageError("report stylesheet must not load external resources")
    return text


def _cost_by_model(summary: Summary) -> str:
    series = [
        (f"{group.provider}/{group.model}", group.cost_usd)
        for group in summary.by_model
        if group.cost_usd is not None
    ]
    return bar_chart("Cost by model", series)


def _cost_by_endpoint(summary: Summary) -> str:
    totals: dict[str, Decimal] = {}
    for group in summary.by_endpoint_model:
        if group.cost_usd is None:
            continue
        key = group.endpoint or "(unknown)"
        totals[key] = totals.get(key, Decimal(0)) + group.cost_usd
    series = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return bar_chart("Cost by endpoint", series)


def _latency(summary: Summary) -> str:
    title_rows: list[tuple[str, float, float, float]] = []
    if not summary.latency_measured:
        return empty_chart("Latency p50 / p95 / p99", "Latency was not measured for this export.")
    for group in summary.by_endpoint_model:
        latency = group.latency()
        if latency.p50 is None or latency.p95 is None or latency.p99 is None:
            continue
        label = f"{group.endpoint or '(unknown)'} · {group.model} (n={latency.n})"
        title_rows.append((label, latency.p50, latency.p95, latency.p99))
    return latency_chart(title_rows)
