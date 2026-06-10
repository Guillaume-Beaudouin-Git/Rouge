"""Tests TDI : z recalculé à la main sur fixture, jambes manquantes."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from collectors.tdi_builder import build_tdi, load_tdi_map, spread_z


def _df(values: np.ndarray) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(values), freq="B")
    return pd.DataFrame({"session": idx, "close": values})


def test_z_recalcule_a_la_main() -> None:
    """Spread plat puis saut connu du dernier point → z = saut / σ(baseline)."""
    n, w = 100, 60
    a = np.full(n, 100.0)
    rng = np.random.default_rng(4)
    noise = rng.normal(0, 0.001, n)
    b = 100.0 * np.exp(-noise)          # spread = log a − log b = noise
    a_s = _df(a).set_index("session")["close"]
    b_s = _df(b).set_index("session")["close"]
    z0, _ = spread_z(a_s, b_s, w)
    # recalcul manuel
    spread = noise
    base = spread[-(w + 1):-1]
    z_manual = (spread[-1] - base.mean()) / base.std(ddof=1)
    assert z0 == pytest.approx(z_manual, abs=1e-9)
    # saut de +5σ sur la dernière session
    b2 = b.copy()
    b2[-1] = b2[-1] * np.exp(-5 * base.std(ddof=1))
    z5, _ = spread_z(a_s, _df(b2).set_index("session")["close"], w)
    assert z5 == pytest.approx(z_manual + 5, abs=0.05)


def test_historique_insuffisant_echoue() -> None:
    a = _df(np.full(30, 100.0)).set_index("session")["close"]
    with pytest.raises(ValueError, match="insuffisant"):
        spread_z(a, a, 60)


def test_jambe_manquante_ligne_excluded() -> None:
    cfg = load_tdi_map()
    n = 120
    rng = np.random.default_rng(2)
    daily = {}
    legs = {leg for d in cfg["divergences"] for leg in (d["a"], d["b"])}
    for leg in legs - {"COPPER"}:       # COPPER manquant volontairement
        daily[leg] = _df(100 * np.exp(rng.normal(0, 0.01, n).cumsum()))
    df, diag = build_tdi(daily, cfg)
    assert len(df) == 12
    assert diag["legs_missing"] == ["COPPER"]
    dead = df[~df["live"]]
    assert len(dead) == 2               # AUD vs cuivre + cuivre vs or
    assert (dead["z"] == 0).all()
    assert dead["note"].str.contains("COPPER").all()
    # tri |z| décroissant
    zs = df["z"].abs().tolist()
    assert zs == sorted(zs, reverse=True)


def test_mapping_12_lignes_obligatoires(tmp_path) -> None:
    import yaml
    from collectors.tdi_builder import MAP_PATH
    cfg = yaml.safe_load(MAP_PATH.read_text(encoding="utf-8"))
    cfg["divergences"] = cfg["divergences"][:10]
    bad = tmp_path / "tdi_map.yaml"
    bad.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    with pytest.raises(RuntimeError, match="12 divergences"):
        load_tdi_map(bad)
