# SPDX-License-Identifier: Apache-2.0
"""Command line. Reads files the user names. No telemetry.

The only network request is a GET of LiteLLM's price file, unless ``--offline``.
"""

from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer

from tracetally import __version__
from tracetally.analysis.engine import Analysis, analyze
from tracetally.analysis.summary import summarize
from tracetally.config import AnalyzeConfig, ReaderConfig, load_check_params, load_tier_map
from tracetally.core.errors import PriceError, ReaderError, UsageError
from tracetally.core.store import TraceStore
from tracetally.pricing.table import ModelPrice, PriceTable, load_prices
from tracetally.readers.base import load_paths, registry, registry_problems

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
    synthetic: Annotated[
        bool,
        typer.Option("--synthetic", help="Label the report as synthetic data."),
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
        synthetic=synthetic,
        demo_seed=None,
    )


@app.command("demo")
def demo_command(
    out: Annotated[
        Path,
        typer.Option("--out", help="Directory for the synthetic files and the report."),
    ] = Path("tracetally-demo"),
    seed: Annotated[
        int,
        typer.Option("--seed", help="Label recorded on the demo. The planted rows do not change."),
    ] = 1,
    no_footer: Annotated[
        bool,
        typer.Option("--no-footer", help="Omit the report footer line."),
    ] = False,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Use the bundled price snapshot. Opens no socket."),
    ] = False,
) -> None:
    """Write the Acme Support Bot sample and a SYNTHETIC DATA report. Local files only."""
    if seed < 0:
        typer.echo("error: --seed must be >= 0", err=True)
        raise typer.Exit(code=EXIT_USAGE)
    from tracetally.demo.generator import write_demo_files

    try:
        written = write_demo_files(out, seed=seed)
    except OSError as exc:
        typer.echo(f"error: could not write demo files: {exc}", err=True)
        raise typer.Exit(code=EXIT_INPUT) from exc
    _emit_report(
        [written["jsonl"]],
        format_name="jsonl",
        endpoint_attr=None,
        offline=offline,
        pricing_config=None,
        discount=None,
        tier_map=written["tiers"],
        config=None,
        only=None,
        skip=None,
        strict=False,
        out=out,
        redact=False,
        include_content=False,
        footer=not no_footer,
        synthetic=True,
        demo_seed=seed,
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
    synthetic: bool,
    demo_seed: int | None,
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
                synthetic=synthetic,
                demo_seed=demo_seed,
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
    if synthetic:
        typer.echo("SYNTHETIC DATA")
        if demo_seed is not None:
            typer.echo(
                f"Acme Support Bot demo. Seed {demo_seed}. Planted rows, not a customer export."
            )
    for path in (written.markdown, written.html, written.findings):
        typer.echo(f"wrote {path}")
    summary_text = written.analysis.summary.to_text()
    findings_text = written.analysis.to_text()
    typer.echo(summary_text, nl=not summary_text.endswith("\n"))
    typer.echo(findings_text, nl=not findings_text.endswith("\n"))
    _exit_if_empty(written.analysis)


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
