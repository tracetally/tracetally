# tracetally

Offline cost and reliability audit for LLM traces you already exported.

Your LLM bill tells you the total. tracetally tells you why.

This tree is the W4 milestone (`0.1.0.dev0`): it reads JSONL, OTLP/JSON,
Langfuse, and OpenAI usage CSV exports, ranks savings and reliability
findings, and writes `report.md`, `report.html`, and `findings.json`.
A cost-only file says which checks it cannot run, and why.

## What it is not

tracetally is not a proxy, gateway, router, SDK, or server. It does not sit
in the request path, hold provider API keys, send telemetry, or fetch prices.
The only I/O is the files you name and the directory you pass to `--out`.
If the machine has no network, every command still runs.

## Install

Python 3.10 or newer.

```bash
python -m pip install -e ".[dev]"
```

The published install (`pipx install tracetally`, `uv tool install tracetally`,
or `pip install tracetally`) lands with the 0.1.0 release. The package does
not call the network at runtime.

## Try it

```bash
tracetally demo
```

That writes a planted Acme Support Bot sample and a report under
`./tracetally-demo/`. The terminal and the report header say **SYNTHETIC DATA**.
The rows are not production traces and they are not a customer result.
`--seed` labels the run. It does not redraw the rows, so the planted dollar
amounts stay fixed.

Open `tracetally-demo/report.html` in a browser. The file is self-contained:
inline CSS, inline SVG, no JavaScript. It does not load images, fonts, or
scripts. The only `https` links are plain anchors (the project page and
"validate with evals"). A browser does not fetch them until you click.

```bash
tracetally summary examples/synthetic_otel.json
tracetally analyze examples/synthetic.jsonl --tier-map examples/tiers.example.yaml
tracetally report examples/synthetic.jsonl --tier-map examples/tiers.example.yaml
tracetally prices
```

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
is one call per line. Both contain the same 235 calls: the original 31
(20 `gpt-4o-mini` on `support.reply`, 5 `claude-sonnet-4-5` with one batch call,
4 `gemini-2.5-flash`, 2 unpriced `acme-private-llm`) plus planted endpoints and
a clean `support.control` endpoint. The OTLP file also has tool spans, which
are not billed. `examples/ground_truth.json` lists the planted dollar ranges.
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

## Analyze

```bash
tracetally analyze examples/synthetic.jsonl --tier-map examples/tiers.example.yaml
tracetally analyze examples/synthetic_otel.json --tier-map examples/tiers.example.yaml --json
tracetally analyze examples/synthetic_langfuse.json
tracetally analyze examples/synthetic_openai_usage.csv --json
```

Each check is a pure function and names the reader capabilities it needs.
A check that cannot run is listed under skipped checks with the reason.
`--only` and `--skip` select checks. `--config` replaces thresholds.
Unknown check names and unknown config keys are a usage error (exit 2).

Findings have a low/mid/high monthly dollar range, effort, quality risk,
confidence, and evidence. Monthly dollars are observed dollars times
`30 / window days` (the window is the first call to the last call, with a
1-day floor). A window under 7 days is still scaled, and the finding says so.

| Check | What it flags | Dollars |
|---|---|---|
| `token_bloat` | input p50 > 4K and input/output > 20, or input p95 > 3× p50 (n ≥ 20) | tokens above p50 × uncached input price × 25/50/75% adoption |
| `cache_opportunity` | a prefix of at least 1,024 tokens shared by ≥ 10% of an endpoint | (input − cached input) × uncached repeated tokens × hit rate 50/75/90% |
| `batch_candidates` | tagged non-interactive, or no user/session id and a recurring hour | list price minus batch price × 50/75/100% of eligible calls |
| `retry_waste` | a failed call followed by the same trace and endpoint within 60s, or a higher `attempt` | list price of the failed attempts (low = mid = high) |
| `agent_loops` | the same span name and args hash ≥ 5 times in one trace, or an LLM-call count above the endpoint p99 | calls beyond the median per trace (low = mid = high) |
| `model_swap` | endpoints in `--tier-map`, repriced at the target model | full list-price delta, always labelled what-if |

`model_swap` is not added to the savings total. The recommendation always
includes `what-if: price only, validate quality with evals`. Without
`--tier-map` the check is skipped.

Latency tails (p99 > 5× p50) and error rates (429 > 1%, 5xx > 0.5%) are
findings with no dollars. If the reader is missing a capability,
`observability_gap` lists the fields to start logging.

The synthetic set plants one issue per dollar check. `support.control` is
clean: analyze does not flag it. Prompt text is hashed at read. A canary
string in the synthetic prompts does not appear in analyze output.

An OpenAI usage CSV has no per-call rows, latency, trace tree, prompts, or
errors. Summary prints cost-only mode (`n/a` latency, error rates not
measured). Analyze skips every check that needs those fields and says the CSV
is cost-only. Langfuse declares the trace capabilities, so those checks run.
Mixing a CSV with a trace file uses the intersection, which is cost-only.

## Report

```bash
tracetally report examples/synthetic.jsonl --tier-map examples/tiers.example.yaml
tracetally report examples/synthetic_openai_usage.csv --out ./csv-report --no-footer
tracetally report examples/synthetic.jsonl --redact
tracetally report examples/synthetic.jsonl --include-content
```

`report` runs the summary and the checks, prints both, and writes three files
in `--out` (default `./tracetally-report/`):

| File | What it is |
|---|---|
| `report.md` | The same audit in Markdown, for a diff or a ticket. |
| `report.html` | One file. Inline CSS and inline SVG. No JavaScript. |
| `findings.json` | `schema_version` 1. The public machine-readable contract. |

The HTML report has an executive summary (spend, top findings, low/mid/high
savings), ranked finding cards (evidence, $/month, effort, quality risk,
confidence, caveats), a reliability section, summary tables, skipped checks,
and a methodology section that names the price table and the date it was
retrieved. What-if rows are labelled and are not added to the savings total.
The footer is one line and comes off with `--no-footer`.

`findings.json` is a `RunResult`. Top-level `skipped_checks` is what this run
skipped, including a missing `--tier-map`, `--only`, and `--skip`. The embedded
`summary` object is the summary document, whose own skip list is capabilities
only. Dollar totals use the same compact decimals as `analyze --json`.
`generated_at` is the only clock in the file.

`--redact` hashes trace ids, user ids, session ids, and input paths with a
per-run salt that is not stored. The same id maps to the same token inside one
report and to a different token next time. The file SHA-256 is kept so you can
match the run to a file you still hold. `--include-content` keeps at most 4000
characters of prompt text per call and prints up to two 500-character excerpts
on each finding. It is not a full prompt export. `--redact` wins: the text is
not kept, and the command says so.

`--synthetic` adds the SYNTHETIC DATA banner. `tracetally demo` sets it.

Exit codes for every command: `0` ok, `2` usage, `3` input or price-file error,
`4` records were read but none could be priced. `report` still writes the
files when the exit code is 4.

## Demo

```bash
tracetally demo --out ./tracetally-demo --seed 1
```

Writes `synthetic.jsonl`, `synthetic_otel.json`, `tiers.yaml`,
`ground_truth.json`, and the three report files. The report is built from the
JSONL file plus the tier map, so the summary and every check, including the
model-swap what-if, all run. `examples/ground_truth.json` lists the planted dollar ranges.
`support.control` is the clean endpoint.

## Other commands

`tracetally prices`, `tracetally prices list`, and `tracetally prices check`
show the loaded price table. `tracetally readers` lists adapters.
`tracetally version` prints the version. Details of the price file are below.

## Getting a file into each reader

tracetally only reads a file you already have. It does not connect to a
collector, a Langfuse server, or the OpenAI API.

- **OTLP JSON.** Export OTLP/JSON from your app or from a collector file
  exporter (one request per line, or one `ExportTraceServiceRequest` document)
  and pass that path. `tracetally report traces.json --format otel`.
- **JSONL.** Write one object per line with the fields below, or pretty-print
  a single object or a JSON array. `tracetally report calls.jsonl --format jsonl`.
- **Langfuse.** Export traces or observations as JSON or CSV from the Langfuse
  UI, then pass the file. `tracetally report observations.json --format langfuse`.
- **OpenAI usage CSV.** Download the usage or costs CSV from the API usage
  dashboard, then pass the file. The run is cost-only.
  `tracetally report usage.csv --format openai_csv`.

Without `--format`, the highest `sniff()` score above 0.5 wins. A tie is an
error that names both readers.

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
| `trace_id`, `parent_id`, `user_id`, `session_id` | no | Group retries, loops, and batch candidates. |
| `interactive` | no | `false` on every call marks an endpoint as a batch candidate. |
| `attempt` | no | A higher attempt after a failure is retry waste. |
| `kind` | no | `tool`, `retriever`, `agent`, or `other` is a span, not an LLM call. |

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

A batch discount is not a flat 50% of the whole cost. It multiplies uncached
input and output. Cached tokens are multiplied only when that model's
`batch_cached` rule is `stack`. The bundled table, checked against the
publisher pages on 2026-10-22:

- OpenAI's batch table publishes a cached-input rate at half the standard
  cached rate for `gpt-5`, `gpt-5-mini`, `gpt-5-nano`, `gpt-5.1`, `gpt-5.2`,
  `gpt-5.4-mini`, and `gpt-5.4-nano`, so those models stack. Other OpenAI rows
  in this file show "-" in that column (`gpt-4o`, `gpt-4.1`, the o-series,
  `gpt-5-pro`, `gpt-5.2-pro`). Their cached tokens stay at the standard
  cached-input price. https://developers.openai.com/api/docs/pricing
- Anthropic says prompt-caching multipliers stack with the 50% Message Batches
  discount, including cache reads. https://platform.claude.com/docs/en/build-with-claude/batch-processing
- Gemini 2.5, `gemini-3-flash-preview`, and `gemini-3.1-pro-preview` keep the
  standard context-caching price on a batch cache hit. `gemini-3.1-flash-lite`
  publishes a batch cache price at half the standard cache price, so that
  model stacks. `gemini-3.5-flash-lite` publishes a batch cache price of
  $0.02, which is neither the standard $0.03 nor half of it, so those cached
  tokens stay at $0.03. https://ai.google.dev/gemini-api/docs/pricing and
  https://ai.google.dev/gemini-api/docs/batch-api

List prices and the batch-times-cache rule were retrieved 2026-10-22. No rate
in the file was invented to fill a blank.

## What is read

tracetally reads only the paths you pass:

- the trace files on the command line
- an optional `--prices` YAML file, merged over the bundled table
- an optional `--tier-map` YAML file
- an optional `--config` YAML file of check thresholds

Prompt and response fields are read in order to hash them (BLAKE2b, 16-byte
digest) and are then dropped. Prefix hashes use a chars/4 estimate only to
pick a bucket. Dollar math uses the reported token counts. Raw prompt text is
not printed, not written, and not sent anywhere unless you pass
`--include-content`. `--redact` also hashes identifiers in the report, as
described above.

The command writes to the terminal and, for `report` and `demo`, to `--out`.
There is no cache outside that directory, no update check, and no telemetry.

## Offline guarantee

Three checks enforce the rule, and they run in CI:

- the test suite starts with sockets disabled (`pytest --disable-socket`)
- ruff's `banned-api` rule rejects imports of `socket`, `urllib.request`,
  `http.client`, `requests`, `httpx`, `aiohttp`, and `subprocess` in `src/`
- `scripts/check_no_http_clients.py` fails if the installed runtime dependency
  tree contains an HTTP client

A separate test parses `report.html` and fails if it contains a script, an
image, a stylesheet link, or any `src` / `href` that would load a remote
resource. The SVG `xmlns` is a namespace, not a fetch. The two allowed `https`
anchors are the project page and the "validate with evals" link.

## Development

```bash
ruff check src tests scripts
mypy src
python -m pytest --disable-socket
```

The socket block, the ruff import ban, and `scripts/check_no_http_clients.py`
are the offline guarantee. CI runs ruff, mypy, and pytest on Python 3.10–3.13
across Linux, macOS, and Windows.

## Validate with evals

Savings ranges and model-swap rows are price estimates. They do not say that a
cheaper model will keep answer quality. Check that with your own evals before
you change a route. The report says this in the methodology section.

## Author

Built by Arockia. If you want this run on your production traces, with a
written, prioritised fix plan, I do LLM cost and reliability audits. The work
stays read-only: you export the files, and nothing is routed through a gateway.

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
