# Contributing

tracetally is an offline, read-only CLI. Changes that add network calls,
telemetry, or a proxy/gateway/server are out of scope.

## Setup

```bash
python -m pip install -e ".[dev]"
```

## Checks

```bash
ruff check src tests scripts
ruff format --check src tests scripts
mypy src
python -m pytest --disable-socket
python scripts/check_no_http_clients.py
python -m build
python scripts/check_package_contents.py dist/*
```

The test suite blocks sockets. `src/tracetally/pricing/fetch.py` is the only
module that may make an HTTP call (one GET of the pinned LiteLLM price file).
Ruff and `scripts/check_no_http_clients.py` fail the build on any other
network import or HTTP call. The wheel and the sdist may contain one data
file, the bundled LiteLLM snapshot. A weekly workflow refreshes that snapshot
and opens a pull request. It does not push to `main`.

CI installs that wheel in a fresh venv and runs `scripts/clean_wheel_smoke.py`.
The live command is `tracetally analyze --offline` on JSONL generated at
runtime by `tests.support.synthetic`. `tracetally run --offline`, the empty
directory check (`No LLM usage file found`, exit 0), and auto-detect stay
disabled until those commands exist.

## Prices

The bundled snapshot is
`src/tracetally/pricing/data/model_prices_and_context_window.json`, a copy of
LiteLLM's price file. The only network call is the GET in
`src/tracetally/pricing/fetch.py`. `--offline` uses that snapshot and opens
no socket. Do not guess a price for a model the snapshot does not list.
Unknown models stay unpriced at runtime. User overrides belong in
`tracetally.toml`. Updating the snapshot is a pull request from the weekly
workflow, never a push to `main` and never a second HTTP client.

## Reports

`report.html` is one file. Do not add a script, a web font, an image, or a
stylesheet link. Inline SVG is drawn in `src/tracetally/report/svg.py`. The
only remote URLs are the two plain anchors in `tracetally.report.links`.
`tests/report/test_html_offline.py` fails the build if a report would load
anything else.

`--include-content` is the only path that may copy prompt text into the
report, and it is capped. The default readers hash that text and drop it.

## Readers

Built-in readers register on the `tracetally.readers` entry-point group.
A third-party reader is a local install of that same group. tracetally does
not download plugins.
