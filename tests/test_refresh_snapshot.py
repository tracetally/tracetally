# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

from tracetally.core.errors import PriceError
from tracetally.pricing.catalog import BUNDLED_NAME
from tracetally.pricing.fetch import LITELLM_PRICES_URL

ROOT = Path(__file__).resolve().parents[1]


def _refresh():
    script = ROOT / "scripts" / "refresh_litellm_snapshot.py"
    spec = importlib.util.spec_from_file_location("refresh_litellm_snapshot", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _body() -> bytes:
    return (
        b'{"gpt-4o-mini":{"litellm_provider":"openai",'
        b'"input_cost_per_token":0.00000015,"output_cost_per_token":0.0000006,'
        b'"mode":"chat"}}\n'
    )


def _tree(tmp_path: Path) -> Path:
    catalog = tmp_path / "src" / "tracetally" / "pricing" / "catalog.py"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        (ROOT / "src" / "tracetally" / "pricing" / "catalog.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    notice = tmp_path / "NOTICE"
    notice.write_text((ROOT / "NOTICE").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "src" / "tracetally" / "pricing" / "data").mkdir()
    return tmp_path


def test_push_refspec_never_targets_main() -> None:
    module = _refresh()
    assert module.push_refspec() == "HEAD:refs/heads/automated/litellm-snapshot"
    for banned in ("main", "master", "MAIN", "refs/heads/main", "release/0.1.0", "feature"):
        with pytest.raises(SystemExit):
            module.push_refspec(banned)
    assert module.main(["--print-push-ref"]) == 0


def test_configured_branch_cannot_be_protected(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _refresh()
    for protected in ("main", "master", "release/0.1.0"):
        monkeypatch.setattr(module, "SNAPSHOT_BRANCH", protected)
        with pytest.raises(SystemExit):
            module.push_refspec()


def test_refresh_rewrites_the_snapshot_date_and_notice(tmp_path: Path) -> None:
    module = _refresh()
    root = _tree(tmp_path)
    today = date(2026, 12, 1)
    written = module.apply_refresh(root, _body().replace(b"\n", b"\r\n"), today)
    assert written == module.snapshot_path(root)
    stored = written.read_bytes()
    assert b"\r" not in stored
    assert stored.endswith(b"\n")
    catalog = (root / "src" / "tracetally" / "pricing" / "catalog.py").read_text(encoding="utf-8")
    assert "BUNDLED_SNAPSHOT_DATE = date(2026, 12, 1)" in catalog
    assert "date(2026, 11, 10)" not in catalog
    notice = (root / "NOTICE").read_text(encoding="utf-8")
    assert "2026-12-01 from" in notice
    assert LITELLM_PRICES_URL in notice
    assert module.apply_refresh(root, stored, date(2026, 12, 2)) is None
    reread = (root / "src" / "tracetally" / "pricing" / "catalog.py").read_text(encoding="utf-8")
    assert reread == catalog


def test_invalid_snapshot_is_not_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _refresh()
    root = _tree(tmp_path)
    catalog = (root / "src" / "tracetally" / "pricing" / "catalog.py").read_text(encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "fetch_prices", lambda: b"not-json")
    assert module.main([]) == 1
    assert not module.snapshot_path(root).exists()
    assert (root / "src" / "tracetally" / "pricing" / "catalog.py").read_text(
        encoding="utf-8"
    ) == catalog


def test_unreadable_snapshot_raises_before_write(tmp_path: Path) -> None:
    module = _refresh()
    root = _tree(tmp_path)
    with pytest.raises(PriceError):
        module.apply_refresh(root, b"\xff", date(2026, 12, 1))
    assert not module.snapshot_path(root).exists()


def test_pr_body_names_the_pinned_url_and_refuses_a_main_push() -> None:
    module = _refresh()
    body = module.pull_request_body(tests="failure")
    assert LITELLM_PRICES_URL in body
    assert BUNDLED_NAME in body
    assert "does not push to `main`" in body
    assert "failure" in body
    workflow = (ROOT / ".github/workflows/refresh-litellm-prices.yml").read_text(encoding="utf-8")
    pushes = [line for line in workflow.splitlines() if "git push" in line]
    assert pushes
    for line in pushes:
        assert "main" not in line
        assert "master" not in line
        assert "$refspec" in line
    assert "--print-push-ref" in workflow
    assert "HEAD:refs/heads/automated/litellm-snapshot" in workflow
    assert "gh pr list" in workflow
    assert "--state open" in workflow
    assert "gh pr create" in workflow
    assert "--base main" in workflow
    assert "curl " not in workflow
    assert module.main(["--print-pr-body", "success"]) == 0
