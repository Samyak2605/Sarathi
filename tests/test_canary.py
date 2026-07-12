from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.asyncio


async def test_canary_skips_cleanly_without_live_credentials(app_settings, tmp_path, monkeypatch):
    import canary.run_canary as run_canary

    monkeypatch.setattr(run_canary, "RESULTS_DIR", tmp_path)
    await run_canary.main()

    report = json.loads((tmp_path / "latest.json").read_text())
    assert report["status"] == "skipped"
