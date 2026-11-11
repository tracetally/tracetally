# tracetally

Your LLM bill tells you the total. tracetally tells you why.

A local CLI that reads LLM usage exports you already have (JSONL, OpenTelemetry, Langfuse, OpenAI usage CSV) and explains cost and reliability: token bloat, caching opportunities, routing what-ifs, batch candidates, agent loops, retry waste, p50/p95/p99 latency, and 429/5xx errors.

## Install

Python 3.10 or newer. Apache-2.0.

```bash
pip install tracetally
```

A checkout uses `python -m pip install -e ".[dev]"`. The version in this tree is `0.1.0.dev0` until the release commit.

## Run

```bash
tracetally run
```

That is the one command. It auto-detects usage exports (JSONL, OpenTelemetry, Langfuse, OpenAI usage CSV) anywhere under the current folder. An explicit path is still allowed:

```bash
tracetally run traces.jsonl
tracetally run ./exports --offline --out ./tracetally-report
tracetally run usage.csv --pricing-config ./tracetally.toml
```

It prices only the models that appear in your export, using LiteLLM's MIT-licensed price JSON (one price-list download, 24-hour cache, bundled snapshot fallback). `--offline` skips the fetch and uses the bundled snapshot. `--out` changes the report directory (default `./tracetally-report/`). `--pricing-config` points at a `tracetally.toml` of aliases, custom prices, discounts, and a static FX rate.

`tracetally run` with no PATH scans the current directory. `tracetally run ./exports` scans that folder. The scan opens `.jsonl`, `.json`, `.csv`, and `.ndjson`, sniffs the format from the first records, and does not follow symlinks. It skips `.git`, `.venv`, `venv`, `node_modules`, `__pycache__`, `dist`, `build`, `.tox`, `.mypy_cache`, `site-packages`, and the report folder. `--max-depth` defaults to 4 (a file in the folder you named is depth 1). `--max-file-size` defaults to 67108864 bytes (64 MiB); a larger file is skipped with a note. Identical file contents are counted once. A record whose request, span, or trace id was already seen in an earlier file is dropped, and that count is stored as `records_dropped` in `findings.json`. Files that share a model and a time window but no ids are kept, with an overlap warning.

Rule-based findings, ranked by estimated dollars saved, are written to `./tracetally-report/` as `findings.json`, `report.md`, and `dashboard.html`. The dashboard is written in that folder and opens in the local browser. `--no-open` leaves it closed. It also stays closed when stdout is not a terminal or when `CI` is set. There is no database. The dashboard is one file: inline CSS, one inline script, no remote assets.

Runs locally. Your data never leaves your machine. The only network request tracetally makes is a price-list download, which `--offline` turns off. No telemetry, no proxy, no LLM calls.

If no usage file is found, the command prints `No LLM usage file found`, a short guide to getting each export, and the "Book a detailed audit" link, then exits 0.

A bad file prints a warning and the rest of the files still run. Exit 0 when the report is written.

`summary` and `analyze` stay as terminal-only commands. They are not aliases of `run`. `report` still writes `report.md`, `report.html`, and `findings.json`. `run` writes the dashboard and opens it unless you pass `--no-open`.

## What it is not

tracetally is not a proxy, gateway, router, middleware, SDK, or server. It does not sit in the request path, hold provider API keys, or send telemetry.

## Pricing honesty

- Self-hosted and unknown models show **cost unknown**, never $0. Other checks still run.
- The report shows the share of calls that were priced.
- Fuzzy matches (for example a stripped date or snapshot suffix) are marked approx.
- `tracetally.toml` holds aliases, custom prices, discounts, and a static FX rate. There is no FX lookup.

A finding whose cost could not be estimated is labelled `cost unknown` and sorts last.

## Book a detailed audit

Every console summary, `report.md`, and dashboard footer ends with **[Book a detailed audit](https://tracetally.github.io/tracetally/audit/)**. That URL is `AUDIT_CTA_URL` in `tracetally/_cta.py`.

## Summary

```bash
tracetally summary traces.jsonl
tracetally summary traces.jsonl --json --offline
tracetally prices
```

The text summary prints:

- cost and tokens by model, and by endpoint × model
- p50 / p95 / p99 latency when a group has at least 20 durations (`n<20` otherwise)
- 429 and 5xx rates
- the share of calls that were priced

`--json` prints the same numbers for scripts. `--strict` fails on the first bad record. The default skips malformed lines and invalid records and names the problem, including the field, in a warning. Prompt text is hashed and dropped. It is not printed.

## Analyze

```bash
tracetally analyze traces.jsonl --tier-map tiers.yaml
tracetally analyze traces.json --json --offline
tracetally analyze observations.json
tracetally analyze usage.csv --json
```

Each check is a pure function and names the reader capabilities it needs. A check that cannot run is listed under skipped checks with the reason. `--only` and `--skip` select checks. `--config` replaces thresholds. Unknown check names and unknown config keys are a usage error (exit 2).

Findings have a low/mid/high monthly dollar range, effort, quality risk, confidence, and evidence. Monthly dollars are observed dollars times `30 / window days` (the window is the first call to the last call, with a 1-day floor). A window under 7 days is still scaled, and the finding says so.

| Check | What it flags | Dollars |
|---|---|---|
| `token_bloat` | input p50 > 4K and input/output > 20, or input p95 > 3× p50 (n ≥ 20) | tokens above p50 × uncached input price × 25/50/75% adoption |
| `cache_opportunity` | a prefix of at least 1,024 tokens shared by ≥ 10% of an endpoint | (input − cached input) × uncached repeated tokens × hit rate 50/75/90% |
| `batch_candidates` | tagged non-interactive, or no user/session id and a recurring hour | list price minus batch price × 50/75/100% of eligible calls |
| `retry_waste` | a failed call followed by the same trace and endpoint within 60s, or a higher `attempt` | list price of the failed attempts (low = mid = high) |
| `agent_loops` | the same span name and args hash ≥ 5 times in one trace, or an LLM-call count above the endpoint p99 | calls beyond the median per trace (low = mid = high) |
| `model_swap` | endpoints in `--tier-map`, repriced at the target model | full list-price delta, always labelled what-if |

`model_swap` is not added to the savings total. The recommendation always includes `what-if: price only, validate quality with evals`. Without `--tier-map` the check is skipped.

Latency tails (p99 > 5× p50) and error rates (429 > 1%, 5xx > 0.5%) are findings with no dollars. If the reader is missing a capability, `observability_gap` lists the fields to start logging.

Prompt text is hashed at read. It is not printed.

An OpenAI usage CSV has no per-call rows, latency, trace tree, prompts, or errors. Summary prints cost-only mode (`n/a` latency, error rates not measured). Analyze skips every check that needs those fields and says the CSV is cost-only. Langfuse declares the trace capabilities, so those checks run. Mixing a CSV with a trace file uses the intersection, which is cost-only.

## Report

```bash
tracetally run
tracetally run traces.jsonl --offline
tracetally report traces.jsonl --tier-map tiers.yaml
tracetally report usage.csv --out ./csv-report --no-footer
tracetally report traces.jsonl --redact
tracetally report traces.jsonl --include-content
```

`run` writes three files in `--out` (default `./tracetally-report/`) and opens the local static dashboard. `report` writes the same markdown and JSON plus an SVG report, and prints the summary:

| File | What it is |
|---|---|
| `report.md` | The audit in Markdown. Every copy ends with "Book a detailed audit". |
| `dashboard.html` | `run` only. One file. Data is in a JSON script. The page script uses `textContent`. |
| `report.html` | `report` only. Inline CSS and inline SVG. No JavaScript. |
| `findings.json` | `schema_version` 1. Price source, URL, date, sha256, currency, and fx_rate. |

The HTML report has an executive summary (spend, top findings, low/mid/high savings), ranked finding cards (evidence, $/month, effort, quality risk, confidence, caveats), a reliability section, summary tables, skipped checks, and a methodology section that names the price table and the date it was retrieved. What-if rows are labelled and are not added to the savings total. The footer is one line and comes off with `--no-footer`.

`findings.json` is a `RunResult`. Top-level `skipped_checks` is what this run skipped, including a missing `--tier-map`, `--only`, and `--skip`. The embedded `summary` object is the summary document, whose own skip list is capabilities only. Dollar totals use the same compact decimals as `analyze --json`. `generated_at` is the only clock in the file.

`--redact` hashes trace ids, user ids, session ids, and input paths with a per-run salt that is not stored. The same id maps to the same token inside one report and to a different token next time. The file SHA-256 is kept so you can match the run to a file you still hold. `--include-content` keeps at most 4000 characters of prompt text per call and prints up to two 500-character excerpts on each finding. It is not a full prompt export. `--redact` wins: the text is not kept, and the command says so.

Exit codes for `summary`, `analyze`, and `report`: `0` ok, `2` usage, `3` input or price-file error, `4` records were read but none could be priced. `report` still writes the files when the exit code is 4. `run` exits `0` when it has written the folder, including when a file was skipped with a warning, and `0` when no usage file is found (it prints the guide instead of a report).

## Other commands

`tracetally prices`, `tracetally prices list`, and `tracetally prices check` show the loaded price table. `tracetally readers` lists adapters. `tracetally version` prints the version. Details of the price file are below.

## Getting a file into each reader

tracetally only reads a file you already have. It does not connect to a collector, a Langfuse server, or the OpenAI API. Put the file under the current folder and run `tracetally run`, or pass its path.

- **OpenAI usage CSV.** Sign in at platform.openai.com, select the organization or project, open Usage, set the date range, click Export, choose Activity data or Cost data, set File format to CSV, then Download. Dates are UTC; a long range may be split into several files. The run is cost-only. Docs: [Exporting monthly usage details from the API Usage Dashboard](https://help.openai.com/en/articles/20001072-exporting-monthly-usage-details-from-the-api-usage-dashboard).
- **Langfuse.** Open the Traces or Observations table, apply any filters, and use Export as CSV or JSON. The export is queued; download it from Project Settings → Exports when it shows Completed. Docs: [Export Data from UI](https://langfuse.com/docs/api-and-data-platform/features/export-from-ui).
- **OpenTelemetry / OpenLLMetry.** Write OTLP JSON traces to a file (UTF-8 JSON lines; each line a `TracesData` object). Docs: [OpenTelemetry Protocol File Exporter](https://opentelemetry.io/docs/specs/otel/protocol/file-exporter/). With the OpenTelemetry Collector, the file exporter's default `format: json` (no compression) writes uncompressed, newline-delimited OTLP JSON. Docs: [File Exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/exporter/fileexporter/README.md). OpenLLMetry emits standard OTLP HTTP; point it at a collector with `TRACELOOP_BASE_URL`, then write those traces to a file as above. Docs: [LLM observability with OpenTelemetry Collector](https://www.traceloop.com/docs/openllmetry/integrations/otel-collector).
- **JSONL.** tracetally's native format: one JSON object per line with the fields below, or a pretty-printed object or JSON array. There is no third-party export into this schema; write or convert your own file.

Without `--format`, the highest `sniff()` score above 0.5 wins. A tie is an error that names both readers.

## JSONL fields

One JSON object per line. A pretty-printed object or a JSON array of objects is accepted too, but one object per line is what streams.

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
| `reported_cost_usd` | no | Billed cost. Used instead of the inferred list price when present. |
| `prompt`, `messages`, `input`, `output` | no | Hashed, then discarded. |
| `trace_id`, `parent_id`, `user_id`, `session_id` | no | Group retries, loops, and batch candidates. |
| `interactive` | no | `false` on every call marks an endpoint as a batch candidate. |
| `attempt` | no | A higher attempt after a failure is retry waste. |
| `kind` | no | `tool`, `retriever`, `agent`, or `other` is a span, not an LLM call. |

Unknown fields are ignored. A missing required field names that field.

## OTLP JSON

`resourceSpans` → `scopeSpans` → `spans`, including one request per line from a collector file exporter. Current `gen_ai.*` attributes are read first (`gen_ai.provider.name`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.usage.cache_read.input_tokens`). Legacy `gen_ai.system`, `gen_ai.usage.prompt_tokens`, and `gen_ai.usage.completion_tokens` are accepted, as are OpenInference (`llm.*`, `openinference.span.kind`) and OpenLLMetry (`traceloop.span.kind`) fallbacks.

`--format jsonl`, `--format otel`, `--format langfuse`, or `--format openai_csv` skips sniffing. Without `--format`, the highest `sniff()` score above 0.5 wins. A tie is an error that names both readers. A canonical JSONL line that only has `trace_id` stays JSONL; Langfuse detection looks for Langfuse fields (`traceId`, `usageDetails`, `type: GENERATION`, `observations`).

A single OTLP document is loaded whole. Above 500 MB the tool warns and still loads it; a JSONL exporter streams.

## Langfuse

JSON and CSV exports of traces and observations. The reader accepts a JSON array, `{"data": [...]}` / `{"observations": [...]}` / `{"traces": [...]}`, one object, or one object per line, and a CSV (comma, semicolon, or tab). `GENERATION` and `EMBEDDING` become calls. `SPAN`, `EVENT`, `AGENT`, `TOOL`, `CHAIN`, `RETRIEVER`, `EVALUATOR`, and `GUARDRAIL` become spans. Nested observations inherit the trace id, name, user, session, and tags.

Token counts come from `usageDetails` / `usage` or from flat columns. A usage unit other than tokens is skipped. Cached tokens are treated as included in the input count. Reasoning tokens are kept and are not added on top of output tokens. `costDetails.total` (or input plus output, or a flat cost column) is stored as `reported_cost_usd`, separate from the list-price cost. Latency is `endTime - startTime`. A `latency` column is seconds, which is how Langfuse documents it.

Column aliases (`Trace ID`, `startTime`, `inputTokens`, …), extra columns, and missing optional fields are accepted. Dates may be ISO-8601 (`T` or a space), `YYYY/MM/DD`, unix time, or an unambiguous slash date. An ambiguous `MM/DD` vs `DD/MM` value is skipped. The default is lenient; `--strict` stops on the first bad record.

## OpenAI usage CSV

Aggregated usage or cost rows. There is no published dashboard header, so the reader accepts the Usage and Costs API names plus aliases (`date`, `snapshot_id`, `n_context_tokens_total`, `n_generated_tokens_total`, `amount`, `amount_value`, `Cached Input Tokens`, `Cost ($)`). `request_count` is the row's request total. `input_tokens` includes cached tokens. Audio token columns are ignored, not folded into the text counts. `amount_cents` is rejected rather than converted. A non-USD currency is skipped. A cost with no token counts is kept. The token counts are left unknown and are not estimated; the billed cost is the call's cost.

The reader declares no capabilities. A run that only loads this format is cost-only: there is no latency, trace tree, prompt hash, or error status to check.

## Prices

Prices come from LiteLLM's MIT-licensed `model_prices_and_context_window.json`. Current list prices are applied only to the models that appear in your export. The only network request tracetally makes is one HTTPS GET of

`https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`

with user agent `tracetally/<version>`, no query string, no body, and no cookies. `HTTPS_PROXY` is honored. `--offline` reads the bundled snapshot and does not open a socket. Otherwise a cache younger than 24 hours is used; a failed fetch (timeout, HTTP error, bad JSON, schema change) falls back to a stale cache, then the snapshot. Reports record the source, URL, date, and sha256. The LiteLLM MIT license is in `NOTICE`.

`tracetally.toml` holds `[aliases]`, custom prices per 1M tokens, `[discounts]`, and a static `currency` / `fx_rate`. `--discount 0.1` (or the config) prints list and effective prices. There is no FX lookup.

Matching order: exact id, provider prefix, LiteLLM `alias_of`, a stripped date or snapshot suffix (labelled approx.), then a user alias. OpenAI `ft:` ids use a listed fine-tune price when one exists, otherwise the base model marked estimate. Bedrock region prefixes and Vertex `@date` names are priced for the provider that billed.

OpenAI and Gemini input counts include cache reads; those reads are subtracted once and priced at the cache rate. OTel input counts include cache reads and cache writes for every provider, including Anthropic. JSONL and Langfuse Anthropic input counts exclude cache reads and cache writes, so those are priced separately, and a cache write uses LiteLLM's `cache_creation_input_token_cost` when it is published. An export with no cache fields is not given any. An over-200k rate applies to that request only. Batch, priority, and flex rates apply only when the export shows that tier. A billed cost in the export wins. Missing token counts are not filled in. A zero list price (including `ollama/*`) and self-hosted names (vLLM, Ollama, TGI, NIM, Hugging Face `org/model`, localhost) are cost unknown; other checks still run. The summary shows the share of calls that were priced.

## What is read

tracetally reads only local files:

- auto-detected usage exports under the current folder, or the paths you pass
- an optional `tracetally.toml` or `--pricing-config` file (aliases, prices, discounts, FX)
- an optional `--tier-map` YAML file
- an optional `--config` YAML file of check thresholds

Prompt and response fields are read in order to hash them (BLAKE2b, 16-byte digest) and are then dropped. Prefix hashes use a chars/4 estimate only to pick a bucket. Dollar math uses the reported token counts. Raw prompt text is not printed, not written, and not sent anywhere unless you pass `--include-content`. `--redact` also hashes identifiers in the report, as described above.

The command writes to the terminal and, for `run` and `report`, to `--out`. There is no database and no telemetry. The price-list cache is the only other file the tool writes, and `--offline` does not use it.

## Network

Runs locally. Your data never leaves your machine. The only network request tracetally makes is a price-list download, which `--offline` turns off. No telemetry, no proxy, no LLM calls.

`--offline` is covered in CI by:

- the test suite starting with sockets disabled (`pytest --disable-socket`)
- ruff's `banned-api` rule, with an exception only for the price-list fetch
- `scripts/check_no_http_clients.py` on the runtime dependency tree, again with that one allowed fetch

A separate test parses `report.html` and fails if it contains a script, an image, a stylesheet link, or any `src` / `href` that would load a remote resource. The SVG `xmlns` is a namespace, not a fetch. The two allowed `https` anchors are the project page and the "validate with evals" link. `dashboard.html` is the one file with a script. Its CSP is `default-src 'none'` and allows that script by sha256, with no `unsafe-inline`.

## Development

```bash
ruff check src tests scripts
mypy src
python -m pytest --disable-socket
```

Pull requests run ruff, mypy, and pytest on Linux while the repository is private. The full matrix, Python 3.10–3.13 on Linux, macOS, and Windows, runs before release.

## Validate with evals

Savings ranges and model-swap rows are price estimates. They do not say that a cheaper model will keep answer quality. Check that with your own evals before you change a route. The report says this in the methodology section.

## Author

Built by Arockia. If you want this run on your production traces, with a written, prioritised fix plan, I do LLM cost and reliability audits. The work stays read-only: you export the files, and nothing is routed through a gateway.

## Release

`pyproject.toml` stays at `0.1.0.dev0` until the commit that is tagged. The version string PyPI receives is that field on the tagged commit, so set it to `0.1.0` in the release commit before tagging.

Pushing a tag that matches `v*` runs `.github/workflows/release.yml`. For this release the tag is `v0.1.0`. The build job runs `python -m build` (hatchling) and uploads the sdist and wheel. The publish job runs only on a `v*` tag. It uses `pypa/gh-action-pypi-publish` with PyPI Trusted Publishing (`id-token: write`) and the GitHub Actions environment named `pypi`. That environment has required reviewers. A reviewer approves it, then the upload runs. The workflow does not use a `PYPI_API_TOKEN` secret. A manual `workflow_dispatch` builds the same artifacts and publishes only when the selected ref is a `v*` tag.

## Project page

`docs/audit/index.html` is the static page for `https://tracetally.github.io/tracetally/audit/`. CSS is inline. The page does not load remote fonts or scripts. Pages is not enabled from this repo.

After the repository is public, an owner turns it on in the GitHub settings (this is a click in the UI, not a workflow):

Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: the default branch (`main`) → Folder: `/docs` → Save.

No custom domain. Once that save succeeds, the page is `https://tracetally.github.io/tracetally/audit/`.

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
