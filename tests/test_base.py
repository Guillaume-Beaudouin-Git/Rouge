"""Tests du socle BaseCollector — aucun appel réseau."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pandas as pd
import pytest

import collectors.base as base
from collectors.base import BaseCollector, FetchError


class DummyCollector(BaseCollector):
    name = "dummy"
    dataset = "dummy"
    max_retries = 3

    def collect(self) -> pd.DataFrame:
        return pd.DataFrame({"x": [1, 2, 3]})


@pytest.fixture()
def collector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DummyCollector:
    monkeypatch.setattr(base, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(base, "LOG_DIR", tmp_path / "_logs")
    # get_logger met le logger en cache au niveau process : on purge ses
    # handlers pour que le FileHandler soit recréé sous le LOG_DIR du test
    logging.getLogger("rouge.dummy").handlers.clear()
    c = DummyCollector()
    c.data_dir = base.DATA_DIR / c.dataset
    c.cache_dir = base.DATA_DIR / "_cache" / c.name
    return c


def _mount_transport(collector: DummyCollector, handler) -> None:
    collector._client = httpx.Client(transport=httpx.MockTransport(handler))


def test_write_parquet_partition_and_idempotence(collector: DummyCollector) -> None:
    p1 = collector.write_parquet(pd.DataFrame({"x": [1]}), partition_date="2026-06-10")
    p2 = collector.write_parquet(pd.DataFrame({"x": [7]}), partition_date="2026-06-10")
    assert p1 == p2
    assert p1.parent.name == "date=2026-06-10"
    df = pd.read_parquet(p1)
    assert list(df["x"]) == [7]
    assert "_collected_at" in df.columns


def test_write_parquet_refuses_empty(collector: DummyCollector) -> None:
    with pytest.raises(ValueError):
        collector.write_parquet(pd.DataFrame())


def test_latest_partition(collector: DummyCollector) -> None:
    assert collector.latest_partition() is None
    collector.write_parquet(pd.DataFrame({"x": [1]}), partition_date="2026-06-08")
    last = collector.write_parquet(pd.DataFrame({"x": [2]}), partition_date="2026-06-10")
    assert collector.latest_partition() == last


def test_fetch_retries_then_succeeds(collector: DummyCollector, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    _mount_transport(collector, handler)
    assert collector.fetch_json("https://example.test/x") == {"ok": True}
    assert calls["n"] == 3


def test_fetch_gives_up_after_max_retries(collector: DummyCollector, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    _mount_transport(collector, lambda req: httpx.Response(503))
    with pytest.raises(FetchError):
        collector.fetch("https://example.test/down")


def test_cache_roundtrip(collector: DummyCollector) -> None:
    collector.cache_ttl = 3600
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"v": 42})

    _mount_transport(collector, handler)
    assert collector.fetch_json("https://example.test/c") == {"v": 42}
    assert collector.fetch_json("https://example.test/c") == {"v": 42}
    assert calls["n"] == 1  # second appel servi par le cache disque


def test_run_returns_false_on_failure(collector: DummyCollector, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: DummyCollector) -> pd.DataFrame:
        raise RuntimeError("flux tombé")

    monkeypatch.setattr(DummyCollector, "collect", boom)
    assert collector.run() is False


def test_logs_are_json_lines(collector: DummyCollector) -> None:
    collector.write_parquet(pd.DataFrame({"x": [1]}), partition_date="2026-06-10")
    log_file = base.LOG_DIR / "dummy.log"
    assert log_file.exists()
    for line in log_file.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        assert {"ts", "level", "msg"} <= rec.keys()
