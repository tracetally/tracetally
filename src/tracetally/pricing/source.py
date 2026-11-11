# SPDX-License-Identifier: Apache-2.0
"""Choose live, cached, or bundled prices. Network I/O stays in ``fetch.py``."""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from tracetally.pricing.catalog import (
    BUNDLED_SNAPSHOT_DATE,
    Catalog,
    bundled_bytes,
    parse_price_bytes,
    sha256_hex,
)
from tracetally.pricing.fetch import LITELLM_PRICES_URL, fetch_prices

PriceSource = Literal["live", "cache", "bundled"]
_FRESH = timedelta(hours=24)
_CACHE_NAME = "model_prices_and_context_window.json"


@dataclass(frozen=True)
class Provenance:
    source: PriceSource
    url: str
    retrieved_on: date
    sha256: str


@dataclass(frozen=True)
class PriceDocument:
    catalog: Catalog
    provenance: Provenance
    warnings: tuple[str, ...] = ()


def cache_dir() -> Path:
    """User cache directory. ``XDG_CACHE_HOME`` wins on every platform."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "tracetally"
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "tracetally" / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "tracetally"
    return Path.home() / ".cache" / "tracetally"


def acquire_prices(*, offline: bool, cache_path: Path | None = None) -> PriceDocument:
    """Resolve the price file. Never raises for a network or cache failure.

    ``--offline`` reads the bundled snapshot and does not open a socket.
    Otherwise a cache younger than 24 hours is used. A fetch is attempted
    when the cache is missing or stale. Timeout, HTTP errors, invalid JSON,
    and a schema change fall through to a stale cache, then the bundle.
    """
    directory = cache_path if cache_path is not None else cache_dir()
    cached = directory / _CACHE_NAME
    if offline:
        return _bundled()
    notes: list[str] = []
    fresh = _read_cache(cached, fresh_only=True, notes=notes)
    if fresh is not None:
        return _note(fresh, notes)
    try:
        body = fetch_prices()
        parsed = parse_price_bytes(body)
    except Exception as exc:
        notes.append(f"live price file was not used ({type(exc).__name__})")
        stale = _read_cache(cached, fresh_only=False, notes=notes)
        if stale is not None:
            return _note(stale, notes)
        return _note(_bundled(), notes)
    _write_cache(cached, body)
    return PriceDocument(
        catalog=parsed.catalog,
        provenance=Provenance(
            source="live",
            url=LITELLM_PRICES_URL,
            retrieved_on=datetime.now(timezone.utc).date(),
            sha256=sha256_hex(body),
        ),
        warnings=tuple(notes) + tuple(parsed.warnings),
    )


@lru_cache(maxsize=1)
def _bundled() -> PriceDocument:
    body = bundled_bytes()
    parsed = parse_price_bytes(body)
    return PriceDocument(
        catalog=parsed.catalog,
        provenance=Provenance(
            source="bundled",
            url=LITELLM_PRICES_URL,
            retrieved_on=BUNDLED_SNAPSHOT_DATE,
            sha256=sha256_hex(body),
        ),
        warnings=tuple(parsed.warnings),
    )


def _note(document: PriceDocument, notes: list[str]) -> PriceDocument:
    if not notes:
        return document
    return replace(document, warnings=tuple(notes) + tuple(document.warnings))


def _read_cache(path: Path, *, fresh_only: bool, notes: list[str]) -> PriceDocument | None:
    if not path.is_file():
        return None
    fetched_on = _fetched_on(path)
    if fetched_on is None:
        return None
    age = datetime.now(timezone.utc) - fetched_on
    if fresh_only and age >= _FRESH:
        return None
    try:
        body = path.read_bytes()
        parsed = parse_price_bytes(body)
    except Exception as exc:
        notes.append(f"price cache was unreadable and was removed ({type(exc).__name__})")
        _discard_cache(path)
        return None
    return PriceDocument(
        catalog=parsed.catalog,
        provenance=Provenance(
            source="cache",
            url=LITELLM_PRICES_URL,
            retrieved_on=fetched_on.date(),
            sha256=sha256_hex(body),
        ),
        warnings=tuple(parsed.warnings),
    )


def _fetched_on(path: Path) -> datetime | None:
    meta = _meta_path(path)
    if meta.is_file():
        try:
            text = meta.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            text = ""
        for line in text.splitlines():
            if line.startswith("fetched_at="):
                raw = line.split("=", 1)[1].strip()
                try:
                    parsed = datetime.fromisoformat(raw)
                except ValueError:
                    break
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
    try:
        modified = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(modified, timezone.utc)


def _write_cache(path: Path, body: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, body)
        fetched = datetime.now(timezone.utc).isoformat()
        _atomic_write(
            _meta_path(path),
            f"fetched_at={fetched}\nsha256={sha256_hex(body)}\n".encode(),
        )
    except OSError:
        return


def _discard_cache(path: Path) -> None:
    for candidate in (path, _meta_path(path)):
        try:
            candidate.unlink()
        except OSError:
            continue


def _atomic_write(path: Path, body: bytes) -> None:
    """Write ``body`` via a unique temp file in the same directory, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def _meta_path(path: Path) -> Path:
    return path.with_name(path.name + ".meta")
