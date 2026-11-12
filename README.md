# tracetally

Your LLM bill tells you the total. tracetally tells you why.

## Install

Python 3.10 or newer. Apache-2.0.

```bash
pip install tracetally
```

## Quickstart

```bash
tracetally run
```

With no path, `run` scans the current directory and sniffs each file's format. It writes `findings.json`, `report.md`, and `dashboard.html` in `tracetally-report/`, and opens the dashboard in your browser. `--offline` uses the bundled price snapshot and opens no socket.

Pass your own files or a directory to scan that path instead. `run` accepts:

| Flag | What it does |
|---|---|
| `--offline` | Bundled price snapshot. Opens no socket. |
| `--out` | Report directory. Default `./tracetally-report/`. |
| `--pricing-config` | `tracetally.toml`: aliases, custom prices, discounts, and a static FX rate. |
| `--max-depth` | How far below a scan root to read. A file in the named folder is depth 1. Default 4. |
| `--max-file-size` | Skip a scanned file larger than this many bytes. Default 67108864 (64 MiB). |
| `--no-open` | Do not open `dashboard.html`. Also skipped when stdout is not a terminal or `CI` is set. |

The scan opens `.jsonl`, `.json`, `.csv`, and `.ndjson`. It sniffs the format and does not follow symlinks. It skips `.git`, `.venv`, `venv`, `node_modules`, `__pycache__`, `dist`, `build`, `.tox`, `.mypy_cache`, `site-packages`, and the report folder. Identical file contents are counted once. A repeated request, span, or trace id is dropped; that count is `records_dropped` in `findings.json`.

If nothing is found, `run` prints `No LLM usage file found`, a short export guide, and exits 0. A bad file is a warning; the other files still run. Exit 0 when the report folder is written. Exit 2 is a usage error. Exit 3 is an input or price-file error.

## Privacy

Runs locally. Your data never leaves your machine. The only network request tracetally makes is a price-list download, which `--offline` turns off. No telemetry, no proxy, no LLM calls.

That download is one HTTPS GET of LiteLLM's price file, `model_prices_and_context_window.json`. `--offline` reads the bundled snapshot and does not open a socket. Otherwise a cache younger than 24 hours is reused; a failed fetch falls back to a stale cache, then the snapshot. tracetally is not a gateway, router, middleware, SDK, or server, and it does not hold provider API keys.

## Inputs

Without `--format`, each file is sniffed. These names are the registered readers. `--format` on `summary`, `analyze`, and `report` forces one of them. `run` sniffs; it has no `--format` flag.

| Format | `--format` | What you give it |
|---|---|---|
| JSONL | `jsonl` | One JSON object per line, or a JSON array. Native fields include `model`, `start_time`, `input_tokens`, and `output_tokens`. |
| OpenTelemetry | `otel` | OTLP JSON, including newline-delimited `TracesData` from a file exporter. |
| Langfuse | `langfuse` | A traces or observations export, JSON or CSV. |
| OpenAI usage CSV | `openai_csv` | A usage or cost CSV. Cost only: no latency, traces, prompts, or errors. |

An OpenAI usage CSV is cost-only. Mixing it with a trace file keeps the intersection, which is cost-only.

## Outputs

`tracetally run` writes three files in `--out` and opens `dashboard.html` in your browser unless `--no-open` applies:

| File | Command | What it is |
|---|---|---|
| `findings.json` | `run`, `report` | Ranked findings. `schema_version` 1. Includes the price source and `records_dropped`. |
| `report.md` | `run`, `report` | The same audit in Markdown. |
| `dashboard.html` | `run` | One file: inline CSS, one inline script, no remote assets. |
| `report.html` | `report` | Inline CSS and inline SVG. No JavaScript. |

```bash
tracetally report traces.jsonl --offline --out ./tracetally-report
tracetally report traces.jsonl --redact
tracetally report traces.jsonl --include-content
tracetally report traces.jsonl --no-footer
```

`--redact` hashes trace, user, and session ids and input paths with a per-run salt that is not stored. `--include-content` keeps truncated prompt excerpts. `--redact` wins if both are set. `--no-footer` drops the one-line footer.

`report` also takes `--format`, `--endpoint-attr`, `--offline`, `--pricing-config`, `--discount`, `--tier-map`, `--config`, `--only`, `--skip`, and `--strict`.

## Other commands

`summary` and `analyze` print to the terminal. They are not aliases of `run`.

```bash
tracetally summary traces.jsonl --json --offline
tracetally analyze traces.jsonl --tier-map tiers.yaml --only token_bloat
tracetally prices
tracetally prices list --offline
tracetally prices check --max-age-days 30
tracetally readers
tracetally version
```

`summary` and `analyze` share `--format`, `--endpoint-attr`, `--offline`, `--pricing-config`, `--discount`, `--json`, and `--strict`. `analyze` also takes `--tier-map`, `--config`, `--only`, and `--skip`.

`--discount` is a fraction off list price (`0.1` is 10%). `--tier-map` is a YAML map of endpoint to another model; that check is a price what-if and is not added to the savings total. `--only` and `--skip` may be repeated. Unknown check names are a usage error.

Exit codes for `summary`, `analyze`, and `report`: `0` ok, `2` usage, `3` input or price-file error, `4` records were read but none could be priced. `report` still writes its files on exit 4.

`prices` with no subcommand lists the loaded table. `prices list` accepts `--offline`, `--pricing-config`, and `--json`. `prices check` accepts `--offline`, `--pricing-config`, and `--max-age-days`.

Unknown models and self-hosted names are **cost unknown**, not $0. There is no live FX lookup. A `tracetally.toml` FX rate is a static number.

## Audits

For a written plan on exports you share, see [Book a detailed audit](https://tracetally.github.io/tracetally/audit/). Console text, `report.md`, and the dashboard footer use that same link. The page is `docs/audit/index.html`.

## Contributing

This tree is `0.1.0.dev0` until the release commit. A checkout uses `python -m pip install -e ".[dev]"`.

See [CONTRIBUTING.md](CONTRIBUTING.md). Local checks:

```bash
ruff check src tests scripts
ruff format --check src tests scripts
mypy src
python -m pytest --disable-socket
```

## Author

Built by Reegan.

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
