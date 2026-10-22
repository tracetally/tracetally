# SPDX-License-Identifier: Apache-2.0
"""Command line. Reads files the user names. Opens no sockets."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from tracetally import __version__
from tracetally.analysis.summary import summarize
from tracetally.config import ReaderConfig
from tracetally.core.errors import PriceError, ReaderError, UsageError
from tracetally.pricing.table import ModelPrice, PriceTable, load_prices
from tracetally.readers.base import load_paths, registry, registry_problems

app = typer.Typer(
    name="tracetally",
    help=(
        "Offline cost and reliability audit for exported LLM traces. "
        "Reads files you already have. Makes no network calls and sends no telemetry."
    ),
    no_args_is_help=True,
    add_completion=False,
)
prices_app = typer.Typer(
    help="List the bundled price table, or check it. A --prices file is merged over the bundle.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=False,
)
app.add_typer(prices_app, name="prices")

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_UNPRICED = 4


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
    prices: Annotated[
        Path | None,
        typer.Option("--prices", help="YAML price file merged over the bundled table."),
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
        table = load_prices(prices)
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
        prices_list(prices=None, json_output=False)


@prices_app.command("list")
def prices_list(
    prices: Annotated[
        Path | None,
        typer.Option("--prices", help="Override price file."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
) -> None:
    """List models in the loaded price table."""
    table = _load_or_exit(prices)
    rows = [_price_row(table, price) for price in table.iter_models()]
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "price_table_id": table.display_id,
                    "retrieved_on": table.retrieved_on.isoformat(),
                    "models": rows,
                },
                indent=2,
            )
        )
        return
    typer.echo(
        f"price table {table.display_id}  retrieved {table.retrieved_on.isoformat()}  "
        f"models {len(rows)}"
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
    prices: Annotated[
        Path | None,
        typer.Option("--prices", help="Override price file."),
    ] = None,
    max_age_days: Annotated[
        int | None,
        typer.Option("--max-age-days", help="Fail when the table is older than this many days."),
    ] = None,
) -> None:
    """Validate the price table. Every price must have source_url and retrieved_on."""
    table = _load_or_exit(prices)
    models = table.iter_models()
    typer.echo(
        f"ok {table.display_id} models={len(models)} retrieved={table.retrieved_on.isoformat()} "
        f"age_days={table.age_days(date.today())}"
    )
    if max_age_days is not None:
        try:
            table.assert_fresh(date.today(), max_age_days=max_age_days)
        except PriceError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=EXIT_INPUT) from exc


def _load_or_exit(path: Path | None) -> PriceTable:
    try:
        return load_prices(path)
    except PriceError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=EXIT_INPUT) from exc


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
    }
