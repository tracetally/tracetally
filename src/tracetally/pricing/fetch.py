# SPDX-License-Identifier: Apache-2.0
"""The only network call in tracetally.

One HTTPS GET of LiteLLM's published price file. No query string, no body,
no cookies, and nothing from a user file or environment variable is placed
on the request. ``HTTPS_PROXY`` is honored by the stdlib proxy handler.
"""

from __future__ import annotations

import urllib.error
import urllib.request

from tracetally import __version__
from tracetally.core.errors import PriceError

LITELLM_PRICES_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
TIMEOUT_SECONDS = 10.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the only request is the pinned URL."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: object,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        raise PriceError(f"price host returned redirect {code}")


def fetch_prices(*, timeout: float = TIMEOUT_SECONDS) -> bytes:
    """GET the pinned LiteLLM price file. Raises on any network or HTTP failure."""
    if timeout <= 0:
        raise PriceError("timeout must be positive")
    proxies = urllib.request.getproxies()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(proxies),
        _NoRedirect(),
    )
    request = urllib.request.Request(
        LITELLM_PRICES_URL,
        headers={"User-Agent": f"tracetally/{__version__}"},
        method="GET",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if status != 200:
                raise PriceError(f"price host returned {status}")
            body = response.read()
    except PriceError:
        raise
    except urllib.error.HTTPError as exc:
        raise PriceError(f"price host returned {exc.code}") from exc
    except Exception as exc:
        raise PriceError(f"price fetch failed: {exc}") from exc
    if not isinstance(body, bytes) or not body:
        raise PriceError("price host returned an empty body")
    return body
