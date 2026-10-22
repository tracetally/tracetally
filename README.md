# tracetally

Offline cost and reliability audit for LLM traces you already exported.

Your LLM bill tells you the total. tracetally tells you why.

This tree is the W2 milestone (`0.1.0.dev0`): it reads JSONL, OTLP/JSON,
Langfuse, and OpenAI usage CSV exports and prints a summary. It does not yet
write the audit report or run the savings checks. A cost-only file says which
checks it cannot run, and why.

## What it is not

tracetally is not a proxy, gateway, router, SDK, or server. It does not sit
in the request path, hold provider API keys, send telemetry, or fetch prices.
The only I/O is the files you name and the summary it prints. If the machine
has no network, the summary still runs.

## Install

Python 3.10 or newer.

```bash
python -m pip install -e ".[dev]"
```

The published install (`pipx install tracetally`) lands with the 0.1.0 release.

## Summary

Two synthetic Acme Support Bot exports are checked in. They are labelled
synthetic on purpose. They are not production traces and they are not a
customer result.

```bash
tracetally summary examples/synthetic_otel.json
tracetally summary examples/synthetic.jsonl --json
tracetally summary examples/synthetic_langfuse.json
tracetally summary examples/synthetic_openai_usage.csv
tracetally prices
```

`examples/synthetic_otel.json` is an OTLP/JSON document. `examples/synthetic.jsonl`
is one call per line. Both contain the same 31 calls (20 `gpt-4o-mini`, 5
`claude-sonnet-4-5` with one batch call, 4 `gemini-2.5-flash`, 2 unpriced
`acme-private-llm`). The OTLP file also has one tool span, which is not billed.
`examples/synthetic_langfuse.json` and `examples/synthetic_openai_usage.csv`
are small stand-ins for those export shapes. They are not real account exports.

The text summary prints:

- cost and tokens by model, and by endpoint × model
- p50 / p95 / p99 latency when a group has at least 20 durations (`n<20` otherwise)
- 429 and 5xx rates

`--json` prints the same numbers for scripts. `--strict` fails on the first bad
record. The default skips malformed lines and invalid records and names the
problem, including the field, in a warning. Prompt text is hashed and dropped.
It is not printed.

Other commands: `tracetally readers`, `tracetally version`, `tracetally prices`,
`tracetally prices list`, `tracetally prices check`.

A file that has no per-call rows, latency, or trace tree is printed as
cost-only. Latency cells say `n/a`, error rates say they were not measured,
and the planned checks (`token_bloat`, `cache_opportunity`, `batch_candidates`,
`retry_waste`, `agent_loops`, `model_swap`, `latency_percentiles`,
`error_rates`) are listed as skipped with the capability they needed. Those
checks are not implemented yet. JSONL, OTLP, and Langfuse runs stay in trace
mode and list no skips.

Exit codes: `0` ok, `2` usage, `3` input or price-file error, `4` records were
read but none could be priced.

## JSONL fields

One JSON object per line. A pretty-printed object or a JSON array of objects
is accepted too, but one object per line is what streams.

| Field | Required | Notes |
|---|---|---|
| `model` | yes | As observed. Unknown ids are not priced. |
| `start_time` | yes | ISO-8601, or unix seconds / milliseconds / nanoseconds. |
| `input_tokens`, `output_tokens` | yes | Non-negative integers. |
| `provider` | no | `openai`, `anthropic`, `gemini`. Missing stays unpriced. |
| `endpoint` | no | Defaults to `(unknown)`. |
| `duration_ms` | no | Latency. Percentiles need 20 samples. |
| `cached_input_tokens`, `reasoning_tokens` | no | Cached tokens use the cached price when one is published. Reasoning tokens are shown and are not added on top of `output_tokens`. |
| `http_status`, `error_type`, `status` | no | `429` and `5xx` drive the error rates. |
| `is_batch` | no | Applies the provider batch discount from the price file when that discount is published. |
| `reported_cost_usd` | no | Shown separately from the computed cost. |
| `prompt`, `messages`, `input`, `output` | no | Hashed, then discarded. |

Unknown fields are ignored. A missing required field names that field.

## OTLP JSON

`resourceSpans` → `scopeSpans` → `spans`, including one request per line from a
collector file exporter. Current `gen_ai.*` attributes are read first
(`gen_ai.provider.name`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`,
`gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`). Legacy
`gen_ai.system`, `gen_ai.usage.prompt_tokens`, and `gen_ai.usage.completion_tokens`
are accepted, as are OpenInference (`llm.*`, `openinference.span.kind`) and
OpenLLMetry (`traceloop.span.kind`) fallbacks.

`--format jsonl`, `--format otel`, `--format langfuse`, or `--format openai_csv`
skips sniffing. Without `--format`, the highest `sniff()` score above 0.5 wins.
A tie is an error that names both readers. A canonical JSONL line that only has
`trace_id` stays JSONL; Langfuse detection looks for Langfuse fields
(`traceId`, `usageDetails`, `type: GENERATION`, `observations`).

A single OTLP document is loaded whole. Above 500 MB the tool warns and still
loads it; a JSONL exporter streams.

## Langfuse

JSON and CSV exports of traces and observations. The reader accepts a JSON
array, `{"data": [...]}` / `{"observations": [...]}` / `{"traces": [...]}`,
one object, or one object per line, and a CSV (comma, semicolon, or tab).
`GENERATION` and `EMBEDDING` become calls. `SPAN`, `EVENT`, `AGENT`, `TOOL`,
`CHAIN`, `RETRIEVER`, `EVALUATOR`, and `GUARDRAIL` become spans. Nested
observations inherit the trace id, name, user, session, and tags.

Token counts come from `usageDetails` / `usage` or from flat columns. A usage
unit other than tokens is skipped. Cached tokens are treated as included in
the input count. Reasoning tokens are kept and are not added on top of output
tokens. `costDetails.total` (or input plus output, or a flat cost column) is
stored as `reported_cost_usd`, separate from the list-price cost. Latency is
`endTime - startTime`. A `latency` column is seconds, which is how Langfuse
documents it.

Column aliases (`Trace ID`, `startTime`, `inputTokens`, …), extra columns, and
missing optional fields are accepted. Dates may be ISO-8601 (`T` or a space),
`YYYY/MM/DD`, unix time, or an unambiguous slash date. An ambiguous
`MM/DD` vs `DD/MM` value is skipped. The default is lenient; `--strict` stops
on the first bad record.

## OpenAI usage CSV

Aggregated usage or cost rows. There is no published dashboard header, so the
reader accepts the Usage and Costs API names plus aliases (`date`,
`snapshot_id`, `n_context_tokens_total`, `n_generated_tokens_total`, `amount`,
`amount_value`). `request_count` is the row's request total. `input_tokens`
includes cached tokens. Audio token columns are ignored, not folded into the
text counts. `amount_cents` is rejected rather than converted. A non-USD
currency is skipped. A cost with no token counts is kept, with tokens recorded
as 0 and `reported_cost_usd` preserved.

The reader declares no capabilities. A run that only loads this format is
cost-only: there is no latency, trace tree, prompt hash, or error status to
check.

## Prices

`src/tracetally/pricing/data/prices-2026-10.yaml` is the bundled list-price
table for OpenAI, Anthropic, and Gemini, retrieved 2026-10-22 from each
provider's pricing page. Every model price and every batch discount has
`source_url` and `retrieved_on`. `tracetally prices` prints the loaded rows,
including cached-input and reasoning rates. `reasoning`, when set, is the
published output rate for models that bill thinking tokens as output. It is
not added again on top of `output_tokens`. Anthropic does not publish a
separate reasoning rate, so those rows leave it unset. Models whose page
did not give one verifiable text rate are omitted: OpenAI ids with a "272K"
split and no integer threshold, Gemini prices that change on a later date,
and Anthropic names that are not a confirmed API id. `gpt-3.5-turbo-1106` is
not on the OpenAI page and is not listed.

Resolution is exact id, then alias, then one trailing date suffix
(`-YYYY-MM-DD`, `-YYYYMMDD`, `@YYYY…`). There is no prefix match, so an unknown
model stays unpriced and the summary says so. Azure OpenAI and Vertex AI are
not priced from these lists. `--prices yours.yaml` merges over the bundled
table and your entries win. A user price still needs `source_url` and
`retrieved_on`.

Long-context rates apply only where the file publishes a token threshold
(`gemini-2.5-pro` and `gemini-3.1-pro-preview` above 200k input tokens,
`claude-haiku-5-5` above 100k).
Audio-specific, priority, flex, and regional uplifts are not applied.

## Development

```bash
ruff check src tests scripts
mypy src
python -m pytest --disable-socket
```

The socket block, the ruff import ban, and `scripts/check_no_http_clients.py`
are the offline guarantee. CI runs ruff, mypy, and pytest on Python 3.10–3.13
across Linux, macOS, and Windows.

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
