# SPDX-License-Identifier: Apache-2.0
"""Command line. Reads files the user names. No telemetry.

The only network request is a GET of LiteLLM's price file, unless ``--offline``.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import webbrowser
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer

from tracetally import __version__
from tracetally._cta import AUDIT_CTA_TEXT, AUDIT_CTA_URL
from tracetally.analysis.engine import Analysis, analyze, cost_label, rank_findings
from tracetally.analysis.summary import summarize
from tracetally.config import AnalyzeConfig, ReaderConfig, load_check_params, load_tier_map
from tracetally.core.errors import PriceError, ReaderError, UsageError
from tracetally.core.money import format_usd
from tracetally.core.store import TraceStore
from tracetally.core.terminal import terminal_safe as _terminal_safe
from tracetally.pricing.table import ModelPrice, PriceTable, load_prices
from tracetally.readers.base import load_paths, registry, registry_problems
from tracetally.readers.dedup import dropped_message, load_deduped
from tracetally.readers.discover import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_FILE_SIZE,
    FoundRow,
    format_found_table,
    format_skipped,
    scan_directories,
)
from tracetally.readers.guide import no_export_text

app = typer.Typer(
    name="tracetally",
    help=(
        "Cost and reliability audit for exported LLM traces. "
        "Reads files you already have. Sends no telemetry. "
        "The only network request is the LiteLLM price file; --offline uses the bundled snapshot."
    ),
    no_args_is_help=True,
    add_completion=False,
)
prices_app = typer.Typer(
    help="List the loaded price table, or check it. Overrides come from tracetally.toml.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
)
app.add_typer(prices_app, name="prices")

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_UNPRICED = 4
_PRICING_CONFIG_HELP = "tracetally.toml: aliases, prices, discounts, FX."
_DISCOUNT_HELP = "Fraction off list price (0.1 is 10%). Shows list and effective."


@app.command("run")
def run_command(
    files: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Trace files or a directory to scan. With no PATH, scan the current directory.",
        ),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Use the bundled price snapshot. Opens no socket."),
    ] = False,
    out: Annotated[
        Path,
        typer.Option("--out", help="Directory for findings.json, report.md, and dashboard.html."),
    ] = Path("tracetally-report"),
    pricing_config: Annotated[
        Path | None,
        typer.Option("--pricing-config", help=_PRICING_CONFIG_HELP),
    ] = None,
    max_depth: Annotated[
        int,
        typer.Option(
            "--max-depth",
            min=0,
            help="Levels below a scan root to read. A file in that folder is depth 1. Default 4.",
        ),
    ] = DEFAULT_MAX_DEPTH,
    max_file_size: Annotated[
        int,
        typer.Option(
            "--max-file-size",
            min=0,
            help=(
                "Skip a scanned file larger than this many bytes. "
                f"Default {DEFAULT_MAX_FILE_SIZE} (64 MiB)."
            ),
        ),
    ] = DEFAULT_MAX_FILE_SIZE,
    no_open: Annotated[
        bool,
        typer.Option(
            "--no-open",
            help="Do not open dashboard.html. Skipped when stdout is not a terminal or CI is set.",
        ),
    ] = False,
) -> None:
    """Read traces, price them, and write a ranked report. Bad files are warnings."""
    from tracetally.report.render import ReportOptions, write_run

    roots, explicit = _run_targets(files)
    try:
        scanned = scan_directories(
            roots,
            max_depth=max_depth,
            max_file_size=max_file_size,
            out_dir=out,
        )
    except UsageError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=EXIT_USAGE) from exc
    if not explicit and not scanned.found:
        typer.echo(no_export_text())
        raise typer.Exit(code=EXIT_OK)

    try:
        loaded = load_deduped([*explicit, *[item.path for item in scanned.found]], ReaderConfig())
    except UsageError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=EXIT_USAGE) from exc
    rows = [
        FoundRow(
            path=item.label,
            format_name=item.format_name,
            records=item.records,
            size=item.size,
        )
        for item in loaded.files
    ]
    table_text = format_found_table(rows)
    if table_text:
        typer.echo(table_text)
    skipped_text = format_skipped([*scanned.skipped, *loaded.skipped])
    if skipped_text:
        typer.echo(skipped_text)
    for message in loaded.overlap:
        typer.echo(f"warning: {_terminal_safe(message)}")
    if loaded.store.records_dropped:
        typer.echo(_terminal_safe(dropped_message(loaded.store.records_dropped)))

    try:
        for warning in loaded.store.warnings:
            if warning.startswith("file skipped:"):
                typer.echo(f"warning: {_terminal_safe(warning)}", err=True)
        table = load_prices(offline=offline, pricing_config=pricing_config)
        for warning in table.warnings:
            typer.echo(f"warning: {_terminal_safe(warning)}", err=True)
        result = analyze(loaded.store, table)
        written = write_run(
            loaded.store,
            result,
            ReportOptions(
                out=out,
                generated_at=datetime.now(timezone.utc),
                dashboard=True,
            ),
        )
    except UsageError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=EXIT_USAGE) from exc
    except (ReaderError, PriceError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=EXIT_INPUT) from exc
    if _dashboard_should_open(no_open=no_open):
        _open_dashboard(written.dashboard)
    _print_run_summary(written.analysis, out)


@app.command("summary")
def summary_command(
    files: Annotated[
        list[Path],
        typer.Argument(help="Trace files. JSONL, OTLP JSON, Langfuse export, or OpenAI usage CSV."),
    ],
    format_name: Annotated[
        str | None,
        typer.Option(
            "--format",
            help="Force jsonl, otel, langfuse, or openai_csv. Default: sniff each file.",
        ),
    ] = None,
    endpoint_attr: Annotated[
        str | None,
        typer.Option("--endpoint-attr", help="Attribute or JSONL field used as the endpoint."),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Use the bundled price snapshot. Opens no socket."),
    ] = False,
    pricing_config: Annotated[
        Path | None,
        typer.Option("--pricing-config", help=_PRICING_CONFIG_HELP),
    ] = None,
    discount: Annotated[
        float | None,
        typer.Option("--discount", help=_DISCOUNT_HELP),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Fail on the first malformed line or invalid record."),
    ] = False,
) -> None:
    """Cost and tokens by model, latency percentiles, and 429/5xx rates."""
    if not files:
        raise typer.BadParameter("provide at least one trace file")
    try:
        store = load_paths(
            list(files),
            ReaderConfig(
                endpoint_attr=endpoint_attr,
                strict=strict,
                format_name=format_name,
            ),
        )
        table = load_prices(
            offline=offline,
            pricing_config=pricing_config,
            discount=_discount_rate(discount),
        )
        result = summarize(store, table)
    except (ReaderError, PriceError, UsageError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=EXIT_INPUT) from exc
    rendered = result.to_json() if json_output else result.to_text()
    typer.echo(rendered, nl=not rendered.endswith("\n"))
    _echo_cta(json_output=json_output)
    if result.calls == 0:
        typer.echo("error: no LLM calls were read", err=True)
        raise typer.Exit(code=EXIT_INPUT)
    if result.priced_calls == 0:
        typer.echo("error: no priced records", err=True)
        raise typer.Exit(code=EXIT_UNPRICED)


@app.command("analyze")
def analyze_command(
    files: Annotated[
        list[Path],
        typer.Argument(help="Trace files. JSONL, OTLP JSON, Langfuse export, or OpenAI usage CSV."),
    ],
    format_name: Annotated[
        str | None,
        typer.Option("--format", help="Force jsonl or otel. Default: sniff each file."),
    ] = None,
    endpoint_attr: Annotated[
        str | None,
        typer.Option("--endpoint-attr", help="Attribute or JSONL field used as the endpoint."),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Use the bundled price snapshot. Opens no socket."),
    ] = False,
    pricing_config: Annotated[
        Path | None,
        typer.Option("--pricing-config", help=_PRICING_CONFIG_HELP),
    ] = None,
    discount: Annotated[
        float | None,
        typer.Option("--discount", help=_DISCOUNT_HELP),
    ] = None,
    tier_map: Annotated[
        Path | None,
        typer.Option("--tier-map", help="YAML map of endpoint to a cheaper model. What-if only."),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="YAML thresholds for the checks."),
    ] = None,
    only: Annotated[
        list[str] | None,
        typer.Option("--only", help="Run only this check. Repeat for several."),
    ] = None,
    skip: Annotated[
        list[str] | None,
        typer.Option("--skip", help="Skip this check. Repeat for several."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Fail on the first malformed line or invalid record."),
    ] = False,
) -> None:
    """Rank cost and reliability findings. Model swap is labelled what-if."""
    if not files:
        raise typer.BadParameter("provide at least one trace file")
    try:
        _store, result = _open_analysis(
            files,
            format_name=format_name,
            endpoint_attr=endpoint_attr,
            offline=offline,
            pricing_config=pricing_config,
            discount=discount,
            tier_map=tier_map,
            config=config,
            only=only,
            skip=skip,
            strict=strict,
        )
    except UsageError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=EXIT_USAGE) from exc
    except (ReaderError, PriceError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=EXIT_INPUT) from exc
    rendered = result.to_json() if json_output else result.to_text()
    typer.echo(rendered, nl=not rendered.endswith("\n"))
    _echo_cta(json_output=json_output)
    _exit_if_empty(result)


@app.command("report")
def report_command(
    files: Annotated[
        list[Path],
        typer.Argument(help="Trace files. JSONL, OTLP JSON, Langfuse export, or OpenAI usage CSV."),
    ],
    format_name: Annotated[
        str | None,
        typer.Option(
            "--format", help="Force jsonl, otel, langfuse, or openai_csv. Default: sniff."
        ),
    ] = None,
    endpoint_attr: Annotated[
        str | None,
        typer.Option("--endpoint-attr", help="Attribute or JSONL field used as the endpoint."),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Use the bundled price snapshot. Opens no socket."),
    ] = False,
    pricing_config: Annotated[
        Path | None,
        typer.Option("--pricing-config", help=_PRICING_CONFIG_HELP),
    ] = None,
    discount: Annotated[
        float | None,
        typer.Option("--discount", help=_DISCOUNT_HELP),
    ] = None,
    tier_map: Annotated[
        Path | None,
        typer.Option("--tier-map", help="YAML map of endpoint to a cheaper model. What-if only."),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="YAML thresholds for the checks."),
    ] = None,
    only: Annotated[
        list[str] | None,
        typer.Option("--only", help="Run only this check. Repeat for several."),
    ] = None,
    skip: Annotated[
        list[str] | None,
        typer.Option("--skip", help="Skip this check. Repeat for several."),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Fail on the first malformed line or invalid record."),
    ] = False,
    out: Annotated[
        Path,
        typer.Option("--out", help="Directory for report.md, report.html, and findings.json."),
    ] = Path("tracetally-report"),
    redact: Annotated[
        bool,
        typer.Option("--redact", help="Hash trace, user, and session ids and input paths."),
    ] = False,
    include_content: Annotated[
        bool,
        typer.Option(
            "--include-content",
            help="Keep truncated prompt text in the report. Ignored when --redact is set.",
        ),
    ] = False,
    no_footer: Annotated[
        bool,
        typer.Option("--no-footer", help="Omit the one-line footer."),
    ] = False,
) -> None:
    """Write report.md, report.html, and findings.json. Prints the summary and findings."""
    if not files:
        raise typer.BadParameter("provide at least one trace file")
    _emit_report(
        files,
        format_name=format_name,
        endpoint_attr=endpoint_attr,
        offline=offline,
        pricing_config=pricing_config,
        discount=discount,
        tier_map=tier_map,
        config=config,
        only=only,
        skip=skip,
        strict=strict,
        out=out,
        redact=redact,
        include_content=include_content,
        footer=not no_footer,
    )


@app.command("readers")
def readers_command() -> None:
    """List readers registered on the tracetally.readers entry-point group."""
    found = registry()
    if not found:
        typer.echo("error: no readers registered", err=True)
        raise typer.Exit(code=EXIT_INPUT)
    for name in sorted(found):
        caps = ", ".join(sorted(cap.value for cap in found[name].capabilities))
        typer.echo(f"{name}: {caps}")
    for problem in registry_problems:
        typer.echo(f"warning: reader {problem}", err=True)


@app.command("version")
def version_command() -> None:
    """Print the tracetally version."""
    typer.echo(__version__)


@prices_app.callback()
def prices_command(ctx: typer.Context) -> None:
    """List models in the loaded price table."""
    if ctx.invoked_subcommand is None:
        prices_list(offline=False, pricing_config=None, json_output=False)


@prices_app.command("list")
def prices_list(
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Use the bundled price snapshot. Opens no socket."),
    ] = False,
    pricing_config: Annotated[
        Path | None,
        typer.Option("--pricing-config", help=_PRICING_CONFIG_HELP),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """List models in the loaded price table."""
    table = _load_or_exit(offline=offline, pricing_config=pricing_config)
    rows = [_price_row(table, price) for price in table.iter_models()]
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "price_table_id": table.display_id,
                    "price_source": table.provenance.source,
                    "price_url": table.provenance.url,
                    "retrieved_on": table.retrieved_on.isoformat(),
                    "sha256": table.provenance.sha256,
                    "models": rows,
                },
                indent=2,
            )
        )
        return
    typer.echo(
        f"price table {table.display_id}  source {table.provenance.source}  "
        f"retrieved {table.retrieved_on.isoformat()}  models {len(rows)}"
    )
    for row in rows:
        cached = row["cached_input"] if row["cached_input"] is not None else "-"
        reasoning = row["reasoning"] if row["reasoning"] is not None else "-"
        typer.echo(
            f"{row['provider']}/{row['model']}  in {row['input']}  cached {cached}  "
            f"reasoning {reasoning}  out {row['output']}  "
            f"{row['retrieved_on']}  {row['source_url']}"
        )


@prices_app.command("check")
def prices_check(
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Use the bundled price snapshot. Opens no socket."),
    ] = False,
    pricing_config: Annotated[
        Path | None,
        typer.Option("--pricing-config", help=_PRICING_CONFIG_HELP),
    ] = None,
    max_age_days: Annotated[
        int | None,
        typer.Option("--max-age-days", help="Fail when the table is older than this many days."),
    ] = None,
) -> None:
    """Validate the loaded price table and print its provenance."""
    table = _load_or_exit(offline=offline, pricing_config=pricing_config)
    models = table.iter_models()
    typer.echo(
        f"ok {table.display_id} source={table.provenance.source} models={len(models)} "
        f"retrieved={table.retrieved_on.isoformat()} sha256={table.provenance.sha256} "
        f"age_days={table.age_days(date.today())}"
    )
    if max_age_days is not None:
        try:
            table.assert_fresh(date.today(), max_age_days=max_age_days)
        except PriceError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=EXIT_INPUT) from exc


def _open_analysis(
    files: list[Path],
    *,
    format_name: str | None,
    endpoint_attr: str | None,
    offline: bool,
    pricing_config: Path | None,
    discount: float | None,
    tier_map: Path | None,
    config: Path | None,
    only: list[str] | None,
    skip: list[str] | None,
    strict: bool,
    include_content: bool = False,
) -> tuple[TraceStore, Analysis]:
    store = load_paths(
        list(files),
        ReaderConfig(
            endpoint_attr=endpoint_attr,
            strict=strict,
            format_name=format_name,
            include_content=include_content,
        ),
    )
    table = load_prices(
        offline=offline,
        pricing_config=pricing_config,
        discount=_discount_rate(discount),
    )
    selected = AnalyzeConfig(
        tier_map=load_tier_map(tier_map) if tier_map is not None else {},
        params=load_check_params(config) if config is not None else AnalyzeConfig().params,
        only=frozenset(only) if only else None,
        skip=frozenset(skip or []),
    )
    return store, analyze(store, table, selected)


def _emit_report(
    files: list[Path],
    *,
    format_name: str | None,
    endpoint_attr: str | None,
    offline: bool,
    pricing_config: Path | None,
    discount: float | None,
    tier_map: Path | None,
    config: Path | None,
    only: list[str] | None,
    skip: list[str] | None,
    strict: bool,
    out: Path,
    redact: bool,
    include_content: bool,
    footer: bool,
) -> None:
    from tracetally.report.render import ReportOptions, write_report

    if include_content and redact:
        typer.echo(
            "warning: --redact omits raw prompt text; --include-content was not applied",
            err=True,
        )
    try:
        store, result = _open_analysis(
            files,
            format_name=format_name,
            endpoint_attr=endpoint_attr,
            offline=offline,
            pricing_config=pricing_config,
            discount=discount,
            tier_map=tier_map,
            config=config,
            only=only,
            skip=skip,
            strict=strict,
            include_content=include_content and not redact,
        )
        written = write_report(
            store,
            result,
            ReportOptions(
                out=out,
                redact=redact,
                include_content=include_content and not redact,
                content_requested=include_content,
                footer=footer,
                generated_at=datetime.now(timezone.utc),
                redact_salt=secrets.token_hex(16) if redact else None,
            ),
        )
    except UsageError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=EXIT_USAGE) from exc
    except (ReaderError, PriceError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=EXIT_INPUT) from exc
    for path in (written.markdown, written.html, written.findings):
        typer.echo(f"wrote {_terminal_safe(path)}")
    summary_text = written.analysis.summary.to_text()
    findings_text = written.analysis.to_text()
    typer.echo(summary_text, nl=not summary_text.endswith("\n"))
    typer.echo(findings_text, nl=not findings_text.endswith("\n"))
    _echo_cta(json_output=False)
    _exit_if_empty(written.analysis)


def _run_targets(files: list[Path] | None) -> tuple[list[Path], list[Path]]:
    """Directories are scanned. Named files are read as given. No args scans cwd."""
    if not files:
        return [Path.cwd()], []
    roots: list[Path] = []
    explicit: list[Path] = []
    for path in files:
        try:
            is_directory = path.is_dir()
        except OSError:
            explicit.append(path)
            continue
        if is_directory:
            roots.append(path)
        else:
            explicit.append(path)
    return roots, explicit


def _dashboard_should_open(*, no_open: bool) -> bool:
    """Open only on an interactive terminal, and never under CI or --no-open."""
    if no_open or os.environ.get("CI"):
        return False
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _open_dashboard(path: Path) -> None:
    """Open dashboard.html with a file:// URI. A failure still exits 0."""
    try:
        opened = bool(webbrowser.open(path.resolve().as_uri()))
    except (OSError, ValueError, webbrowser.Error, RuntimeError):
        opened = False
    if not opened:
        typer.echo(f"dashboard: {_terminal_safe(path.resolve())}")


def _echo_cta(*, json_output: bool) -> None:
    """Text summaries end with the CTA. JSON keeps stdout parseable."""
    line = f"{AUDIT_CTA_TEXT}: {AUDIT_CTA_URL}"
    if json_output:
        typer.echo(line, err=True)
        return
    typer.echo(line)


def _print_run_summary(result: Analysis, out: Path) -> None:
    summary = result.summary
    typer.echo(
        f"tracetally {__version__}  calls {summary.calls}  "
        f"priced {summary.priced_calls}/{summary.calls}  "
        f"{format_usd(summary.cost_usd)} {_terminal_safe(summary.currency)}"
    )
    ranked = rank_findings(list(result.findings))
    if not ranked:
        typer.echo("No findings.")
    for index, item in enumerate(ranked, start=1):
        what_if = " (what-if)" if item.what_if else ""
        title = _terminal_safe(item.title)
        label = _terminal_safe(cost_label(item, summary.currency))
        typer.echo(f"{index}. {title}{what_if}  {label}")
    typer.echo(f"report: {_terminal_safe(out)}")
    typer.echo(f"{AUDIT_CTA_TEXT}: {AUDIT_CTA_URL}")


def _exit_if_empty(result: Analysis) -> None:
    if result.summary.calls == 0:
        typer.echo("error: no LLM calls were read", err=True)
        raise typer.Exit(code=EXIT_INPUT)
    if result.summary.priced_calls == 0:
        typer.echo("error: no priced records", err=True)
        raise typer.Exit(code=EXIT_UNPRICED)


def _load_or_exit(*, offline: bool, pricing_config: Path | None) -> PriceTable:
    try:
        return load_prices(offline=offline, pricing_config=pricing_config)
    except (PriceError, UsageError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=EXIT_INPUT) from exc


def _discount_rate(value: float | None) -> Decimal | None:
    if value is None:
        return None
    rate = Decimal(str(value))
    if rate < 0 or rate >= 1:
        raise UsageError("--discount must be a fraction in [0, 1)")
    return rate


def _price_row(table: PriceTable, price: ModelPrice) -> dict[str, object]:
    bucket = table.providers[price.provider]
    return {
        "provider": price.provider,
        "model": price.model,
        "input": format(price.input, "f"),
        "cached_input": None if price.cached_input is None else format(price.cached_input, "f"),
        "reasoning": None if price.reasoning is None else format(price.reasoning, "f"),
        "output": format(price.output, "f"),
        "source_url": price.source_url,
        "retrieved_on": price.retrieved_on.isoformat(),
        "aliases": list(price.aliases),
        "batch_discount": None
        if bucket.batch_discount is None
        else format(bucket.batch_discount, "f"),
        "batch_cached": price.batch_cached,
    }
