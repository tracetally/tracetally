# tracetally

Offline cost and reliability audit for LLM traces you already exported.

Your LLM bill tells you the total. tracetally tells you why.

This tree is the W1 milestone (`0.1.0.dev0`): it reads traces and prints a
summary. It does not yet write the audit report or run the savings checks.

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
is one call per line. Both contain the same 31 calls (20 `gpt-4o-mini`, 5
`claude-sonnet-4-5` with one batch call, 4 `gemini-2.5-flash`, 2 unpriced
`acme-private-llm`). The OTLP file also has one tool span, which is not billed.

The text summary prints:

- cost and tokens by model, and by endpoint × model
- p50 / p95 / p99 latency when a group has at least 20 durations (`n<20` otherwise)
- 429 and 5xx rates

`--json` prints the same numbers for scripts. `--strict` fails on the first bad
record. The default skips malformed lines and invalid records and names the
problem, including the field, in a warning. Prompt text is hashed and dropped.
It is not printed.

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
