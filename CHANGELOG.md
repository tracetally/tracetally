# Changelog

## 0.1.0.dev0

W3 analysis checks, on top of the W2 readers. Not a release.

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
