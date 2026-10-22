# tracetally

Offline cost and reliability audit for LLM traces you already exported.

Your LLM bill tells you the total. tracetally tells you why.

This tree is the W3 milestone (`0.1.0.dev0`): it reads traces, prints a
summary, and ranks savings checks. It does not yet write the audit report.

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
```

`examples/synthetic_otel.json` is an OTLP/JSON document. `examples/synthetic.jsonl`
is one call per line. Both contain the same 235 calls: the original 31
(20 `gpt-4o-mini` on `support.reply`, 5 `claude-sonnet-4-5` with one batch call,
4 `gemini-2.5-flash`, 2 unpriced `acme-private-llm`) plus planted endpoints and
a clean `support.control` endpoint. The OTLP file also has tool spans, which
are not billed. `examples/ground_truth.json` lists the planted dollar ranges.

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

Other commands: `tracetally readers`, `tracetally version`, `tracetally prices list`,
`tracetally prices check`.

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

`--format jsonl` or `--format otel` skips sniffing. Without `--format`, the
highest `sniff()` score above 0.5 wins. A tie is an error that names both readers.

A single OTLP document is loaded whole. Above 500 MB the tool warns and still
loads it; a JSONL exporter streams.

## Prices

`src/tracetally/pricing/data/prices-2026-10.yaml` is a starter list-price table
for OpenAI, Anthropic, and Gemini, retrieved 2026-10-15. Every model price and
every batch discount has `source_url` and `retrieved_on`.

Resolution is exact id, then alias, then one trailing date suffix
(`-YYYY-MM-DD`, `-YYYYMMDD`, `@YYYY…`). There is no prefix match, so an unknown
model stays unpriced and the summary says so. Azure OpenAI and Vertex AI are
not priced from these lists. `--prices yours.yaml` merges over the bundled
table and your entries win. A user price still needs `source_url` and
`retrieved_on`.

Long-context rates apply only where the file publishes a token threshold
(`gemini-2.5-pro` above 200k input tokens, `claude-haiku-5-5` above 100k).
Audio-specific, priority, flex, and regional uplifts are not applied.

A batch discount is not a flat 50% of the whole cost. It multiplies uncached
input and output. Cached tokens are multiplied only when that model's
`batch_cached` rule is `stack`. The bundled table, checked against the
publisher pages on 2026-10-22:

- OpenAI's batch table publishes a cached-input rate for `gpt-5`, `gpt-5-mini`,
  and `gpt-5-nano` at half the standard cached rate, so those models stack.
  Other OpenAI rows in this file show "-" in that column (`gpt-4o`, `gpt-4.1`,
  the o-series). Their cached tokens stay at the standard cached-input price.
  https://developers.openai.com/api/docs/pricing
- Anthropic says prompt-caching multipliers stack with the 50% Message Batches
  discount, including cache reads. https://platform.claude.com/docs/en/build-with-claude/batch-processing
- Gemini 2.5 batch context-caching prices are the standard context-caching
  prices, and a cache hit in a batch pays the standard context caching rates.
  https://ai.google.dev/gemini-api/docs/batch-api and
  https://ai.google.dev/gemini-api/docs/pricing

List prices in the file were retrieved 2026-10-15. The batch-times-cache rule
was retrieved 2026-10-22. No rate in the file was invented to fill a blank.

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
