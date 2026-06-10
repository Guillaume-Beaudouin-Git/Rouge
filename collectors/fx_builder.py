"""Constructeur FX — force G8 + 28 paires, dérivé du lake quotes.

Chaque devise G8 a sa jambe USD dans le lake (vérifié : EURUSD, GBPUSD,
USDJPY, USDCHF, AUDUSD, USDCAD, NZDUSD). Tous les croisements sont
synthétisés triangulairement via les jambes USD (log-returns additifs) —
les croisées natives du lake (EURGBP, EURJPY, AUDJPY) servent de
validation : corrélation synthétique/natif > 0.99 testée.

Force d'une devise = somme des log-returns des 7 paires sur RET_WINDOW
sessions, signés, pondérés inverse-vol (vol 63 sessions), ×1000 — l'unité
est le pour-mille de panier (1 % de mouvement 10 sessions = 10 points),
calibrée sur l'échelle visuelle du front (barres ±18).
Conflit = le sens de la paire (RET_WINDOW sessions) contredit
sign(force_base − force_quote).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from collectors.base import REPO_ROOT, BaseCollector

FX_FIXTURE = REPO_ROOT / "api" / "demo" / "fx.json"

#: ordre du front (fixture) ; jambe USD : (instrument lake, +1 si C/USD)
G8_LEGS = {
    "USD": None,
    "EUR": ("EURUSD", +1), "JPY": ("USDJPY", -1), "GBP": ("GBPUSD", +1),
    "CHF": ("USDCHF", -1), "AUD": ("AUDUSD", +1), "CAD": ("USDCAD", -1),
    "NZD": ("NZDUSD", +1),
}
G8 = list(G8_LEGS)
#: fenêtre des rendements de force / tendance (sessions)
RET_WINDOW = 10
#: fenêtre de la vol des paires (sessions)
VOL_WINDOW = 63
#: profondeur de l'historique de force servi au front
HIST_LEN = 14


def ccy_usd_logrets(daily: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Log-returns quotidiens de chaque devise vs USD (USD = 0), alignés
    sur l'intersection des sessions des 7 jambes."""
    series = {}
    for ccy, leg in G8_LEGS.items():
        if leg is None:
            continue
        sym, sign = leg
        if sym not in daily:
            raise RuntimeError(f"jambe USD manquante dans quotes_daily : {sym}")
        d = daily[sym].set_index("session")["close"]
        series[ccy] = sign * np.log(d).diff()
    df = pd.DataFrame(series).dropna()
    df["USD"] = 0.0
    return df[G8]


def build_fx(daily: dict[str, pd.DataFrame], iso: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """→ (strength 8 lignes, pairs 28 lignes, diagnostics)."""
    lr = ccy_usd_logrets(daily)
    # log-returns synthétiques de chaque paire ordonnée base/quote
    combos = [(G8[i], G8[j]) for i in range(8) for j in range(i + 1, 8)]
    pair_lr = {f"{b}{q}": lr[b] - lr[q] for b, q in combos}

    # force : somme RET_WINDOW des rendements signés, pondérée inverse-vol
    strength = {}
    for c in G8:
        rets, weights = [], []
        for x in G8:
            if x == c:
                continue
            s = lr[c] - lr[x]
            vol = s.rolling(VOL_WINDOW).std()
            rets.append(s.rolling(RET_WINDOW).sum())
            weights.append(1.0 / vol)
        r = pd.concat(rets, axis=1)
        w = pd.concat(weights, axis=1)
        w = w.div(w.sum(axis=1), axis=0)
        strength[c] = 1000 * (r * w).sum(axis=1)
    sdf = pd.DataFrame(strength).dropna()
    if len(sdf) < HIST_LEN:
        raise RuntimeError(f"historique de force insuffisant ({len(sdf)} sessions)")
    asof = sdf.index.max()

    s_rows = [{"c": c, "iso": iso.get(c), "now": round(float(sdf[c].iloc[-1]), 1),
               "s": [round(float(v), 1) for v in sdf[c].tail(HIST_LEN)]}
              for c in G8]
    s_rows.sort(key=lambda r: r["now"], reverse=True)

    p_rows = []
    for b, q in combos:
        diff = round(float(sdf[b].iloc[-1] - sdf[q].iloc[-1]), 1)
        ret_w = float(pair_lr[b + q].rolling(RET_WINDOW).sum().iloc[-1])
        trend = 1 if ret_w >= 0 else -1
        p_rows.append({"p": b + q, "b": b, "q": q, "diff": diff, "trend": trend,
                       "conflict": bool(np.sign(diff) != 0 and np.sign(diff) != trend)})
    p_rows.sort(key=lambda r: abs(r["diff"]), reverse=True)

    diag = {"asof_session": asof,
            "conflict_rate": round(sum(r["conflict"] for r in p_rows) / len(p_rows), 2)}
    return pd.DataFrame(s_rows), pd.DataFrame(p_rows), diag


def load_daily(root: Path | None = None) -> dict[str, pd.DataFrame]:
    root = root or REPO_ROOT / "data" / "quotes_daily"
    out = {}
    for part in sorted(Path(root).glob("sym=*/part.parquet")):
        out[part.parent.name.removeprefix("sym=")] = pd.read_parquet(part)
    if not out:
        raise RuntimeError("data/quotes_daily vide — lancer quotes_collector d'abord")
    return out


class FxBuilder(BaseCollector):
    name = "fx"
    dataset = "fx_strength"

    def __init__(self) -> None:
        super().__init__()
        self.iso = {r["c"]: r["iso"] for r in
                    json.loads(FX_FIXTURE.read_text(encoding="utf-8"))["strength"]}

    def collect(self) -> pd.DataFrame:
        self._strength, self._pairs, self._diag = build_fx(load_daily(), self.iso)
        return self._strength

    def run(self) -> bool:
        try:
            self.collect()
            strength, pairs, diag = self._strength, self._pairs, self._diag
            asof = pd.Timestamp(diag["asof_session"]).date()
            for df, ds in ((strength, "fx_strength"), (pairs, "fx_pairs")):
                df = df.copy()
                df["asof_session"] = pd.Timestamp(asof)
                part_dir = REPO_ROOT / "data" / ds / f"date={asof}"
                part_dir.mkdir(parents=True, exist_ok=True)
                df.to_parquet(part_dir / "part.parquet", index=False)
            self.log.info("run ok", extra={"ctx": {"asof": str(asof), **{
                k: str(v) for k, v in diag.items()}}})
            return True
        except Exception:
            self.log.error("run failed — donnée précédente conservée (stale)", exc_info=True)
            return False


if __name__ == "__main__":
    raise SystemExit(0 if FxBuilder().run() else 1)
