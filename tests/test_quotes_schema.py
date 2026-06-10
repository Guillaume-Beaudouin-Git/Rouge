"""Contrat de schéma du data lake Dukascopy (Algo_claude) — lecture seule.

Ces tests valident les hypothèses documentées dans api/config/quotes_map.yaml
contre le lake réel ; ils sont sautés si le lake n'est pas monté (CI).
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pandas as pd
import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

M5_DIR = Path(os.getenv("DUKASCOPY_LAKE_DIR", "/nonexistent"))
M1_DIR = Path(os.getenv("DUKASCOPY_M1_DIR", "/nonexistent"))

pytestmark = pytest.mark.skipif(
    not M5_DIR.exists(), reason="lake Dukascopy non monté (DUKASCOPY_LAKE_DIR)"
)


def test_pandas3_lit_le_lake_pandas2_sans_warning() -> None:
    """Premier test du contrat : un fichier écrit sous pandas 2.x doit se
    lire sous pandas 3 sans AUCUN warning (copy-on-write inclus)."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        df = pd.read_parquet(M5_DIR / "EURUSD_m5_close.parquet")
        _ = df["close"].iloc[-1]          # accès colonne + scalaire
        _ = df.tail(10).copy()            # slice + copie
    assert len(df) > 1_000_000


def test_schema_m5_close() -> None:
    """{SYM}_m5_close.parquet : index DatetimeIndex tz-aware (Europe/Prague
    malgré le nom 'timestamp_utc'), colonne unique 'close' float64."""
    for sym in ("EURUSD", "XAUUSD", "US500"):
        df = pd.read_parquet(M5_DIR / f"{sym}_m5_close.parquet")
        assert list(df.columns) == ["close"], sym
        assert df["close"].dtype == "float64", sym
        assert isinstance(df.index, pd.DatetimeIndex), sym
        assert df.index.tz is not None, f"{sym} : index naïf — contrat rompu"
        assert str(df.index.tz) == "Europe/Prague", f"{sym} : tz={df.index.tz}"
        assert df.index.is_monotonic_increasing, sym
        assert (df["close"] > 0).all(), sym


def test_schema_m1_yearly_copper() -> None:
    """COPPER (_cache_duka) : colonne ts_utc naïve UTC + OHLCV."""
    if not M1_DIR.exists():
        pytest.skip("DUKASCOPY_M1_DIR non monté")
    files = sorted(M1_DIR.glob("COPPER_*.parquet"))
    assert files, "aucun fichier COPPER dans _cache_duka"
    df = pd.read_parquet(files[-1])
    assert {"ts_utc", "open", "high", "low", "close"} <= set(df.columns)
    assert pd.api.types.is_datetime64_any_dtype(df["ts_utc"])
    assert df["ts_utc"].dt.tz is None, "ts_utc attendu naïf (UTC implicite)"
    assert (df["close"] > 0).all()


def test_couverture_des_instruments_mappes() -> None:
    """Chaque instrument non exclu du mapping doit exister dans le lake."""
    import yaml
    cfg = yaml.safe_load((REPO_ROOT / "api/config/quotes_map.yaml").read_text(encoding="utf-8"))
    for inst in cfg["instruments"]:
        if "excluded" in inst:
            continue
        if inst["source"] == "m5":
            assert (M5_DIR / f"{inst['file']}_m5_close.parquet").exists(), inst["sym"]
        elif inst["source"] == "m1_yearly":
            if M1_DIR.exists():
                assert list(M1_DIR.glob(f"{inst['file']}_*.parquet")), inst["sym"]
