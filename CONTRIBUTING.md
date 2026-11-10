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
```

The test suite blocks sockets. `src/` is also linted so it cannot import
`socket`, `urllib.request`, `http.client`, `requests`, `httpx`, `aiohttp`,
or `subprocess`.

## Prices

The bundled snapshot is
`src/tracetally/pricing/data/model_prices_and_context_window.json`, a copy of
LiteLLM's price file. The only network call is the GET in
`src/tracetally/pricing/fetch.py`. `--offline` uses that snapshot and opens
no socket. Do not guess a price for a model the snapshot does not list.
Unknown models stay unpriced at runtime. User overrides belong in
`tracetally.toml`.

## Reports

`report.html` is one file. Do not add a script, a web font, an image, or a
stylesheet link. Inline SVG is drawn in `src/tracetally/report/svg.py`. The
only remote URLs are the two plain anchors in `tracetally.report.links`.
`tests/report/test_html_offline.py` fails the build if a report would load
anything else. `dashboard.html` is separate: one inline script, hashed into
a CSP of `default-src 'none'`, and no `unsafe-inline`. The audit link text
and URL live in `tracetally/_cta.py`.

`--include-content` is the only path that may copy prompt text into the
report, and it is capped. The default readers hash that text and drop it.

## Readers

Built-in readers register on the `tracetally.readers` entry-point group.
A third-party reader is a local install of that same group. tracetally does
not download plugins.
