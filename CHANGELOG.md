# Changelog

## 0.1.0.dev0

W3 analysis checks. Not a release.

- `tracetally analyze` ranks token bloat, cache opportunity, batch candidates,
  retry waste, agent loops, and model-swap what-if. Terminal and `--json`.
- Each check declares the reader capabilities it needs. A check that cannot
  run is skipped with a reason. `--only`, `--skip`, `--config`, and `--tier-map`.
- Findings carry a low/mid/high monthly dollar range, effort, quality risk,
  confidence, and evidence. Model swap is labelled what-if and is left out of
  the savings total.
- Batch discounts follow the published cached-input rule per provider. They are
  not a flat 50% of every token. Cached tokens stack only for Anthropic and
  for OpenAI models whose batch table publishes a cached rate (`gpt-5`,
  `gpt-5-mini`, `gpt-5-nano`). Gemini 2.5 and the other OpenAI rows keep the
  standard cached-input price.
- Synthetic exports plant one issue per dollar check plus a clean
  `support.control` endpoint. `examples/ground_truth.json` records the ranges.
- Latency tails, 429/5xx rates, and missing reader capabilities are findings
  with no dollars.

W1 skeleton, still in this tree:

- Apache-2.0 package, Typer CLI, and GitHub Actions for Python 3.10–3.13 on
  Linux, macOS, and Windows.
- Canonical pydantic records (`LLMCall`, `Span`, `Finding`, `RunResult`).
- JSONL and OTLP/JSON readers, registered as entry points, with format sniffing.
- `tracetally summary`: cost and tokens by model and by endpoint, p50/p95/p99
  latency, and 429/5xx rates.
- Bundled OpenAI, Anthropic, and Gemini list prices. Each price has
  `source_url` and `retrieved_on`. Unknown models are left unpriced.
