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

Prices live in `src/tracetally/pricing/data/prices-2026-10.yaml`. Every price
needs `source_url` and `retrieved_on`. Do not guess a price for a model that
is not on the publisher's page. Unknown models stay unpriced at runtime.
Updating the table is a pull request, never a network call from the CLI.

## Readers

Built-in readers register on the `tracetally.readers` entry-point group.
A third-party reader is a local install of that same group. tracetally does
not download plugins.
