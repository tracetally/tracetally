# SPDX-License-Identifier: Apache-2.0
"""JSON loading. Uses orjson when the optional extra is installed."""

from __future__ import annotations

import json
from typing import Any

_orjson: Any = None
try:
    import orjson as _orjson_module

    _orjson = _orjson_module
except ImportError:  # pragma: no cover - depends on the optional extra
    _orjson = None


def loads(data: str | bytes) -> Any:
    if _orjson is not None:
        return _orjson.loads(data)
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data)
