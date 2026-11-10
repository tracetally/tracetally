# SPDX-License-Identifier: Apache-2.0
"""Price fetch behaviour. Sockets stay disabled; urllib is mocked."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.message import Message
from pathlib import Path

import pytest

from tracetally import __version__
from tracetally.pricing.fetch import LITELLM_PRICES_URL, TIMEOUT_SECONDS, fetch_prices
from tracetally.pricing.table import load_bundled, load_prices

_CACHE_NAME = "model_prices_and_context_window.json"


def _body(model: str, per_million: str) -> bytes:
    per_token = float(Decimal(per_million) / Decimal(1_000_000))
    return json.dumps(
        {
            model: {
                "litellm_provider": "openai",
                "input_cost_per_token": per_token,
                "output_cost_per_token": 0,
                "mode": "chat",
            }
        }
    ).encode("utf-8")


class _Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class _Opener:
    def __init__(self, response: _Response | None, error: BaseException | None) -> None:
        self._response = response
        self._error = error

    def open(self, request: urllib.request.Request, timeout: float | None = None) -> _Response:
        captured["calls"] += 1
        captured["request"] = request
        captured["timeout"] = timeout
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


captured: dict[str, object] = {}


def _patch_opener(
    monkeypatch: pytest.MonkeyPatch,
    *,
    body: bytes | None = None,
    error: BaseException | None = None,
) -> None:
    captured.clear()
    captured["calls"] = 0
    captured["handlers"] = None

    def build(*handlers: urllib.request.BaseHandler) -> _Opener:
        captured["handlers"] = handlers
        return _Opener(_Response(body or b"") if error is None else None, error)

    monkeypatch.setattr(urllib.request, "build_opener", build)


def _write_cache(directory: Path, body: bytes, fetched_at: datetime) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _CACHE_NAME
    path.write_bytes(body)
    path.with_name(path.name + ".meta").write_text(
        f"fetched_at={fetched_at.isoformat()}\nsha256=ignored\n",
        encoding="utf-8",
    )


def test_fetch_is_one_get_to_the_pinned_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = _body("gpt-test", "3")
    _patch_opener(monkeypatch, body=body)
    table = load_prices(offline=False, cache_path=tmp_path / "cache")
    assert captured["calls"] == 1
    request = captured["request"]
    assert isinstance(request, urllib.request.Request)
    assert request.full_url == LITELLM_PRICES_URL
    assert "?" not in request.full_url
    assert request.get_method() == "GET"
    assert request.data is None
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["user-agent"] == f"tracetally/{__version__}"
    assert "cookie" not in headers
    assert captured["timeout"] == TIMEOUT_SECONDS
    assert table.provenance.source == "live"
    assert table.provenance.url == LITELLM_PRICES_URL
    assert table.provenance.sha256
    assert table.resolve("openai", "gpt-test") is not None
    cached = (tmp_path / "cache" / _CACHE_NAME).read_bytes()
    assert cached == body


def test_offline_makes_zero_connections(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_opener(monkeypatch, error=AssertionError("network"))
    fresh = tmp_path / "cache"
    _write_cache(fresh, _body("from-cache", "9"), datetime.now(timezone.utc))
    table = load_prices(offline=True, cache_path=fresh)
    assert captured["calls"] == 0
    assert table.provenance.source == "bundled"
    assert table.resolve("openai", "from-cache") is None
    assert table.resolve("openai", "gpt-4o-mini") is not None


def test_https_proxy_is_passed_to_the_opener(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8888")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:8888")
    _patch_opener(monkeypatch, body=_body("gpt-test", "1"))
    fetch_prices()
    handlers = captured["handlers"]
    assert isinstance(handlers, tuple)
    proxies = [
        handler.proxies for handler in handlers if isinstance(handler, urllib.request.ProxyHandler)
    ]
    assert proxies
    assert "http://127.0.0.1:8888" in proxies[0].values()


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        ("timeout", TimeoutError("timed out")),
        ("404", urllib.error.HTTPError(LITELLM_PRICES_URL, 404, "missing", Message(), None)),
        ("bad-json", None),
        ("schema", None),
    ],
)
def test_fetch_failure_falls_back_to_stale_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
    error: BaseException | None,
) -> None:
    if failure == "bad-json":
        _patch_opener(monkeypatch, body=b"{")
    elif failure == "schema":
        _patch_opener(monkeypatch, body=b'{"nope": 1}')
    else:
        _patch_opener(monkeypatch, error=error)
    cache = tmp_path / "cache"
    _write_cache(
        cache,
        _body("from-cache", "6"),
        datetime.now(timezone.utc) - timedelta(days=3),
    )
    table = load_prices(offline=False, cache_path=cache)
    assert table.provenance.source == "cache"
    assert table.provenance.retrieved_on == (datetime.now(timezone.utc) - timedelta(days=3)).date()
    price = table.resolve("openai", "from-cache")
    assert price is not None and price.input == Decimal("6")
    assert captured["calls"] == 1


def test_fetch_failure_falls_back_to_bundled_when_cache_is_unusable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_opener(monkeypatch, error=TimeoutError("timed out"))
    cache = tmp_path / "cache"
    _write_cache(cache, b"not-json", datetime.now(timezone.utc) - timedelta(days=2))
    table = load_prices(offline=False, cache_path=cache)
    assert table.provenance.source == "bundled"
    assert table.provenance.url == LITELLM_PRICES_URL
    assert table.resolve("openai", "gpt-4o-mini") is not None

    empty = tmp_path / "empty"
    table = load_prices(offline=False, cache_path=empty)
    assert table.provenance.source == "bundled"
    assert table.provenance.sha256 == load_bundled().provenance.sha256


def test_fresh_cache_is_used_without_a_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_opener(monkeypatch, error=AssertionError("network"))
    cache = tmp_path / "cache"
    _write_cache(cache, _body("from-cache", "4"), datetime.now(timezone.utc))
    table = load_prices(offline=False, cache_path=cache)
    assert captured["calls"] == 0
    assert table.provenance.source == "cache"
    price = table.resolve("openai", "from-cache")
    assert price is not None and price.input == Decimal("4")
    assert table.provenance.sha256
