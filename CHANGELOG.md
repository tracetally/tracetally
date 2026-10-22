# Changelog

## 0.1.0.dev0

W2 readers and prices. Not a release.

- Langfuse JSON and CSV reader. `GENERATION` and `EMBEDDING` map to calls;
  other observation types map to spans. Costs stay in `reported_cost_usd`.
- OpenAI usage CSV reader in cost-only mode. Checks that need latency, traces,
  prompts, or errors are listed as skipped, with the missing capability.
- Bundled OpenAI, Anthropic, and Gemini prices refreshed on 2026-10-22, with
  cached-input and reasoning rates where the publisher states them. `tracetally
  prices` lists the table. `--prices` still overrides it.

W1 skeleton. Not a release.

- Apache-2.0 package, Typer CLI, and GitHub Actions for Python 3.10–3.13 on
  Linux, macOS, and Windows.
- Canonical pydantic records (`LLMCall`, `Span`, `Finding`, `RunResult`).
- JSONL and OTLP/JSON readers, registered as entry points, with format sniffing.
- `tracetally summary`: cost and tokens by model and by endpoint, p50/p95/p99
  latency, and 429/5xx rates.
- Bundled OpenAI, Anthropic, and Gemini list prices. Each price has
  `source_url` and `retrieved_on`. Unknown models are left unpriced.
