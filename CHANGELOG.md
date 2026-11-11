# Changelog

## 0.1.0.dev0

Prices now come from LiteLLM's published price file, with a bundled snapshot
for offline runs. Not a release.

- The only network request is a GET of
  `https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`.
  `--offline` uses the bundled snapshot and opens no socket. A fresh cache
  (under 24 hours) is reused; timeout, HTTP errors, bad JSON, and a schema
  change fall back to a stale cache, then the snapshot.
- Reports name the price source, URL, date, and sha256, and the share of
  calls that were priced.
- Model matching is exact, then provider prefix, LiteLLM `alias_of`, a
  stripped date or snapshot suffix (`approx.`), then `[aliases]` in
  `tracetally.toml`. OpenAI `ft:` ids use a listed fine-tune price, otherwise
  the base price marked `estimate`.
- Cached input is priced at the cache rate. The over-200k rate applies per
  request. Batch, priority, and flex rates apply only when the export shows
  that tier. A billed cost in the export wins over the inferred price.
  Missing token counts are not estimated. A zero list price, including
  `ollama/*`, and self-hosted endpoints are cost unknown.
- `tracetally.toml` supplies aliases, per-model prices per 1M tokens,
  discounts, and a static currency FX rate. `--discount` prints list and
  effective prices. The hand-kept YAML table is retired.

W4 reports and demo, on top of the W3 checks. Not a release.

- `tracetally report` writes `report.md`, a single-file `report.html` (inline
  CSS, inline SVG, no JavaScript), and `findings.json` (`schema_version` 1).
- The report ranks findings with low/mid/high monthly dollars, effort, quality
  risk, confidence, and evidence. What-if rows stay out of the savings total.
- `--redact` hashes trace, user, and session ids and input paths with a per-run
  salt. `--include-content` keeps truncated prompt excerpts. `--redact` wins.
  `--no-footer` drops the one-line footer. `--synthetic` labels the header.
- `tracetally demo` writes the planted Acme Support Bot files and the report.
  The seed labels the run and does not redraw the rows.
- OpenAI usage CSV accepts the dashboard header `Cached Input Tokens`. Those
  tokens are priced at the cached-input rate instead of being dropped.
- A `v*` tag runs the release workflow. The 0.1.0 publish tag is `v0.1.0`:
  it builds the sdist and wheel and, after the `pypi` environment is
  approved, uploads them with PyPI Trusted Publishing. `docs/index.html` is
  the static project page. Pages stays off until the repo is public and an
  owner selects Deploy from a branch, folder `/docs`.

W3 analysis checks, still in this tree. Not a release.

- `tracetally analyze` ranks token bloat, cache opportunity, batch candidates,
  retry waste, agent loops, and model-swap what-if. Terminal and `--json`.
- Each check declares the reader capabilities it needs. A check that cannot
  run is skipped with a reason. `--only`, `--skip`, `--config`, and `--tier-map`.
  An OpenAI usage CSV is cost-only and says so. Langfuse keeps the trace checks.
- Findings carry a low/mid/high monthly dollar range, effort, quality risk,
  confidence, and evidence. Model swap is labelled what-if and is left out of
  the savings total.
- Batch discounts follow the published cached-input rule. They are not a flat
  50% of every token. Cached tokens stack for Anthropic, for OpenAI models
  whose batch table publishes a cached rate, and for `gemini-3.1-flash-lite`.
  Gemini 2.5 and the other OpenAI rows keep the standard cached-input price.
- Synthetic exports plant one issue per dollar check plus a clean
  `support.control` endpoint. `examples/ground_truth.json` records the ranges.
- Latency tails, 429/5xx rates, and missing reader capabilities are findings
  with no dollars.

W2 readers and prices, still in this tree:

- Langfuse JSON and CSV reader. `GENERATION` and `EMBEDDING` map to calls;
  other observation types map to spans. Costs stay in `reported_cost_usd`.
- OpenAI usage CSV reader in cost-only mode.
- Bundled OpenAI, Anthropic, and Gemini prices refreshed on 2026-10-22, with
  cached-input and reasoning rates where the publisher states them.

W1 skeleton. Not a release.

- Apache-2.0 package, Typer CLI, and GitHub Actions for Python 3.10–3.13 on
  Linux, macOS, and Windows.
- Canonical pydantic records (`LLMCall`, `Span`, `Finding`, `RunResult`).
- JSONL and OTLP/JSON readers, registered as entry points, with format sniffing.
- `tracetally summary`: cost and tokens by model and by endpoint, p50/p95/p99
  latency, and 429/5xx rates.
- Bundled OpenAI, Anthropic, and Gemini list prices. Each price has
  `source_url` and `retrieved_on`. Unknown models are left unpriced.
