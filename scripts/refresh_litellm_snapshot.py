# SPDX-License-Identifier: Apache-2.0
"""Refresh the bundled LiteLLM snapshot. Weekly workflow only.

The HTTP call is ``tracetally.pricing.fetch.fetch_prices`` — the same pinned
URL the package uses. This script does not push. The workflow may push only
the refspec from ``push_refspec``, which rejects ``main``.
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from tracetally.core.errors import PriceError
from tracetally.pricing.catalog import BUNDLED_NAME, parse_price_bytes
from tracetally.pricing.fetch import LITELLM_PRICES_URL, fetch_prices

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_BRANCH = "automated/litellm-snapshot"
_PROTECTED = frozenset({"main", "master"})
_USAGE = "refresh_litellm_snapshot.py [--print-push-ref | --print-pr-body TEXT]"


def snapshot_path(root: Path) -> Path:
    return root / "src" / "tracetally" / "pricing" / "data" / BUNDLED_NAME


def push_refspec(branch: str | None = None) -> str:
    """Remote refspec for the snapshot branch. Never a protected branch."""
    name = SNAPSHOT_BRANCH if branch is None else branch
    if (
        name != SNAPSHOT_BRANCH
        or name in _PROTECTED
        or name.startswith("release/")
        or name.startswith("refs/")
    ):
        raise SystemExit(
            f"error: refusing to push to {name!r}. "
            f"The snapshot refresh may only push {SNAPSHOT_BRANCH}."
        )
    return f"HEAD:refs/heads/{SNAPSHOT_BRANCH}"


def pull_request_body(*, tests: str) -> str:
    return (
        "Weekly refresh of the bundled LiteLLM price snapshot.\n\n"
        f"Fetched `{LITELLM_PRICES_URL}`.\n"
        f"Wrote `src/tracetally/pricing/data/{BUNDLED_NAME}`.\n"
        "Updated `BUNDLED_SNAPSHOT_DATE` and the retrieved date in `NOTICE`.\n\n"
        "This workflow opens a pull request. It does not push to `main` and does not merge.\n\n"
        f"Offline pytest (`--disable-socket`): {tests}\n"
    )


def normalize_snapshot(body: bytes) -> bytes:
    """LF line endings and a trailing newline, so a later fetch of the same file is quiet."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PriceError("price file is not valid UTF-8") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")


def apply_refresh(root: Path, body: bytes, today: date) -> Path | None:
    """Write the snapshot, the catalog date, and NOTICE. Return the path, or None if unchanged."""
    normalized = normalize_snapshot(body)
    parse_price_bytes(normalized)
    path = snapshot_path(root)
    current = path.read_bytes() if path.is_file() else None
    if current == normalized:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(normalized)
    catalog = root / "src" / "tracetally" / "pricing" / "catalog.py"
    catalog.write_text(
        rewrite_snapshot_date(catalog.read_text(encoding="utf-8"), today),
        encoding="utf-8",
    )
    notice = root / "NOTICE"
    notice.write_text(
        rewrite_notice_date(notice.read_text(encoding="utf-8"), today),
        encoding="utf-8",
    )
    return path


def rewrite_snapshot_date(text: str, today: date) -> str:
    pattern = re.compile(
        r"^BUNDLED_SNAPSHOT_DATE = date\(\d{4}, \d{1,2}, \d{1,2}\)$",
        re.MULTILINE,
    )
    replacement = f"BUNDLED_SNAPSHOT_DATE = date({today.year}, {today.month}, {today.day})"
    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit("error: BUNDLED_SNAPSHOT_DATE assignment not found")
    return updated


def rewrite_notice_date(text: str, today: date) -> str:
    pattern = re.compile(r"(retrieved\n)\d{4}-\d{2}-\d{2}( from\n)")
    updated, count = pattern.subn(rf"\g<1>{today.isoformat()}\g<2>", text, count=1)
    if count != 1:
        raise SystemExit("error: NOTICE retrieved date not found")
    if LITELLM_PRICES_URL not in updated:
        raise SystemExit("error: NOTICE lost the pinned LiteLLM URL")
    return updated


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--print-push-ref"]:
        print(push_refspec())
        return 0
    if args[:1] == ["--print-pr-body"]:
        tests = args[1] if len(args) > 1 else "not run"
        if len(args) > 2:
            print(f"usage: {_USAGE}", file=sys.stderr)
            return 2
        print(pull_request_body(tests=tests), end="")
        return 0
    if args:
        print(f"usage: {_USAGE}", file=sys.stderr)
        return 2
    try:
        body = fetch_prices()
        changed = apply_refresh(ROOT, body, datetime.now(timezone.utc).date())
    except PriceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if changed is None:
        print(f"snapshot unchanged ({LITELLM_PRICES_URL})")
    else:
        print(f"updated {changed.relative_to(ROOT).as_posix()} from {LITELLM_PRICES_URL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
