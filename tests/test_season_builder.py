"""Tests saisonnalité : recomputation d'une cellule sur fixture synthétique."""

from __future__ import annotations

import numpy as np
import pandas as pd

from collectors.season_builder import monthly_stats


def test_recompute_cellule_janvier() -> None:
    """3 ans de daily synthétique où janvier fait exactement +2 %, +4 %,
    −1 % → mean janvier = 5/3 %, hit = 2/3."""
    sessions, prices = [], []
    px = 100.0
    jan_targets = {2023: 0.02, 2024: 0.04, 2025: -0.01}
    for d in pd.date_range("2022-12-30", "2025-12-31", freq="B"):
        if d.month == 1:
            # répartit le retour cible de janvier uniformément sur ~22 séances
            px *= (1 + jan_targets[d.year]) ** (1 / 22)
        sessions.append(d)
        prices.append(px)
    close = pd.Series(prices, index=pd.DatetimeIndex(sessions, name="session"))
    stats = monthly_stats(close).set_index("month")

    jan = stats.loc[1]
    # close-à-close mensuel : ~(2+4-1)/3 = 1.67 % (tolérance : la cible est
    # répartie sur les séances ouvrées, ~22 ≠ exactement le mois)
    assert jan["mean_pct"] == pytest_approx(5 / 3, 0.25)
    assert jan["hit_pct"] == pytest_approx(100 * 2 / 3, 0.1)
    assert jan["n_years"] == 3
    # les autres mois sont plats : moyenne ~0, et l'historique compte 3 ans
    assert abs(stats.loc[6, "mean_pct"]) < 0.05
    assert stats["n_years"].max() == 3


def pytest_approx(v: float, tol: float):
    import pytest
    return pytest.approx(v, abs=tol)


def test_mois_absents_sans_nan() -> None:
    """Historique court (6 mois) : les mois sans données sortent à 0, pas NaN."""
    idx = pd.date_range("2026-01-01", "2026-06-05", freq="B")
    close = pd.Series(np.linspace(100, 110, len(idx)), index=idx)
    stats = monthly_stats(close).set_index("month")
    assert len(stats) == 12
    assert not stats["mean_pct"].isna().any()
    assert stats.loc[10, "n_years"] == 0 and stats.loc[10, "mean_pct"] == 0.0
