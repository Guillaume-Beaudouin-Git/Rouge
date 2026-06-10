"""Constructeur TREND — jointure multi-datasets vers data/trend/.

Score composite du front : g = 0.35·mom + 0.20·mac + 0.15·(pos+risk+flow).
- mom  (quotes)  : momentum multi-horizon normalisé par la vol
- risk (quotes)  : régime de vol réalisée (calme = positif)
- pos  (COT)     : percentile 3 ans du positionnement spéculatif, centré
- mac, flow      : collecteurs non branchés → 0 NEUTRE + flag dans meta,
                   jamais une valeur simulée
Les actifs absents du lake (quotes_map.yaml: excluded) sortent en ligne
neutre live=false. Les champs statiques (cat, name, drapeaux) viennent de
la fixture front api/demo/trend.json — source de vérité unique.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from collectors.base import REPO_ROOT, BaseCollector
from collectors.quotes_collector import load_quotes_map

TREND_FIXTURE = REPO_ROOT / "api" / "demo" / "trend.json"
MAC_MAP_PATH = REPO_ROOT / "api" / "config" / "trend_mac.yaml"


def load_mac_map(path: Path = MAC_MAP_PATH) -> dict:
    import yaml
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    front = {r["sym"] for r in json.loads(TREND_FIXTURE.read_text(encoding="utf-8"))}
    missing = front - set(cfg["assets"])
    if missing:
        raise RuntimeError(f"trend_mac.yaml : actifs non mappés : {sorted(missing)}")
    return cfg


def mac_score(sym: str, ccy_scores: dict[str, tuple[float, int]],
              cfg: dict) -> tuple[float, bool]:
    """Composante mac ∈ [-50, 50] depuis les scores de surprise macro.
    ccy_scores : devise → (mom, n). Gate : n >= min_n requis sur CHAQUE
    devise impliquée, sinon (0, False)."""
    rule = cfg["assets"][sym]
    if "neutral" in rule:
        return 0.0, False
    min_n, scale = cfg["min_n"], cfg["scale"]

    def ok(ccy: str) -> float | None:
        mom, n = ccy_scores.get(ccy, (0.0, 0))
        return mom if n >= min_n else None

    if "local" in rule:
        m = ok(rule["local"])
        return (0.0, False) if m is None else (float(np.clip(scale * m, -50, 50)), True)
    mb, mq = ok(rule["base"]), ok(rule["quote"])
    if mb is None or mq is None:
        return 0.0, False
    return float(np.clip(scale * (mb - mq), -50, 50)), True

#: horizons momentum (sessions) et bornes
MOM_HORIZONS = (21, 63, 126, 252)
MOM_CLIP = 4.0
#: fenêtre du régime de vol et de son rang
VOL_WIN, VOL_RANK_WIN = 21, 252
#: longueur de la série g pour le percentile 30 sessions
G_HIST = 31

ACTIVE_COMPONENTS = {"mom": True, "mac": False, "pos": True, "risk": True, "flow": False}


def mom_series(close: pd.Series) -> pd.Series:
    """Momentum multi-horizon ∈ [-50, 50] : moyenne des rendements par
    horizon normalisés par la vol réalisée (échelle racine du temps)."""
    rets = close.pct_change()
    vol = rets.rolling(63).std()
    parts = []
    for h in MOM_HORIZONS:
        m = close.pct_change(h) / (vol * np.sqrt(h))
        parts.append(m.clip(-MOM_CLIP, MOM_CLIP))
    return (50 / MOM_CLIP) * pd.concat(parts, axis=1).mean(axis=1)


def risk_series(close: pd.Series) -> pd.Series:
    """Régime de vol ∈ [-50, 50] : 50 − percentile de la vol 21 sessions
    dans sa fenêtre 1 an (calme = positif, stress = négatif)."""
    vol = close.pct_change().rolling(VOL_WIN).std()
    rank = vol.rolling(VOL_RANK_WIN).rank(pct=True)
    return 50 - 100 * rank


def pos_score(sym: str, cot_pctl: dict[str, float], links: dict) -> tuple[float, bool]:
    """Positionnement ∈ [-50, 50] depuis les percentiles COT.
    direct : pctl−50 ; FX : base − quote (futures cotés vs USD), /2 si croisée.
    Retourne (score, disponible)."""
    link = links.get(sym)
    if not link:
        return 0.0, False
    base, quote = link.get("base"), link.get("quote")
    if "direct" in link:
        p = cot_pctl.get(link["direct"])
        return (0.0, False) if p is None else (p - 50.0, True)
    b = cot_pctl.get(base) if base else None
    q = cot_pctl.get(quote) if quote else None
    if base and b is None or quote and q is None:
        return 0.0, False
    if base and quote:
        return ((b - 50.0) - (q - 50.0)) / 2, True
    if base:
        return b - 50.0, True
    return -(q - 50.0), True


def build_rows(daily: dict[str, pd.DataFrame], cot_pctl: dict[str, float],
               cfg: dict, statics: dict[str, dict],
               ccy_scores: dict[str, tuple[float, int]] | None = None,
               mac_cfg: dict | None = None) -> pd.DataFrame:
    """Assemble la table TREND complète (26 lignes, triée g décroissant)."""
    links = cfg.get("cot_links", {})
    ccy_scores = ccy_scores or {}
    rows = []
    last_session = max(df["session"].max() for df in daily.values()) if daily else None
    for inst in cfg["instruments"]:
        sym = inst["sym"]
        st = statics[sym]
        base = {"cat": st["cat"], "sym": sym, "name": st["name"],
                "f1": st["f1"], "f2": st["f2"]}
        if "excluded" in inst or sym not in daily:
            rows.append({**base, "g": 0.0, "mom": 0.0, "mac": 0.0, "pos": 0.0,
                         "risk": 0.0, "flow": 0.0, "d30": 50, "chg": 0.0,
                         "live": False, "pos_available": False,
                         "mac_available": False, "eff_weight": 0.0,
                         "session": last_session})
            continue
        d = daily[sym].set_index("session")
        close = d["close"]
        mom = mom_series(close)
        risk = risk_series(close)
        pos, pos_ok = pos_score(sym, cot_pctl, links)
        mac, mac_ok = mac_score(sym, ccy_scores, mac_cfg) if mac_cfg else (0.0, False)
        # série g sur G_HIST sessions — pos (COT hebdo) et mac (surprises
        # macro) tenus constants sur la fenêtre (approximation documentée)
        g = 0.35 * mom + 0.20 * mac + 0.15 * (pos + risk + 0.0)
        g_hist = g.dropna().tail(G_HIST)
        if len(g_hist) < 2:
            raise RuntimeError(f"{sym} : historique insuffisant ({len(g_hist)} sessions)")
        g_now, g_prev = g_hist.iloc[-1], g_hist.iloc[-2]
        d30 = int(round(100 * (g_hist <= g_now).mean()))
        rows.append({**base,
                     "g": round(g_now, 1), "mom": round(mom.iloc[-1], 1),
                     "mac": round(mac, 1), "pos": round(pos, 1),
                     "risk": round(risk.iloc[-1], 1), "flow": 0.0,
                     "d30": d30, "chg": round(g_now - g_prev, 1),
                     "live": True, "pos_available": pos_ok,
                     "mac_available": mac_ok,
                     "eff_weight": round(0.35 + 0.15 + 0.15 * pos_ok + 0.20 * mac_ok, 2),
                     "session": close.index.max()})
    out = pd.DataFrame(rows).sort_values("g", ascending=False).reset_index(drop=True)
    # session de référence du build, identique sur toutes les lignes :
    # la plus récente commune n'est pas exigée, on prend le max des live
    # (les retardataires gardent leur dernière session en colonne session)
    out["asof_session"] = last_session
    return out


class TrendBuilder(BaseCollector):
    name = "trend"
    dataset = "trend"

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_quotes_map()
        self.mac_cfg = load_mac_map()
        self.statics = {r["sym"]: r for r in json.loads(TREND_FIXTURE.read_text(encoding="utf-8"))}
        missing = {i["sym"] for i in self.cfg["instruments"]} ^ set(self.statics)
        if missing:
            raise RuntimeError(f"quotes_map.yaml ≠ univers TREND du front : {sorted(missing)}")

    def _load_daily(self) -> dict[str, pd.DataFrame]:
        root = self.data_dir.parent / "quotes_daily"
        out = {}
        for part in sorted(root.glob("sym=*/part.parquet")):
            sym = part.parent.name.removeprefix("sym=")
            out[sym] = pd.read_parquet(part)
        if not out:
            raise RuntimeError("data/quotes_daily vide — lancer quotes_collector d'abord")
        return out

    def _load_cot_pctl(self) -> dict[str, float]:
        parts = sorted((self.data_dir.parent / "cot").glob("date=*/part.parquet"))
        if not parts:
            self.log.warning("COT absent — composante pos neutre")
            return {}
        df = pd.read_parquet(parts[-1])
        latest = df.sort_values("report_date").groupby("sym").tail(1)
        # même fenêtre que v_cot : les 156 dernières semaines, point
        # courant inclus (partitions ordonnées par date de rapport)
        hist = pd.concat([pd.read_parquet(p) for p in parts[-160:]], ignore_index=True)
        pctl = {}
        for sym, net in latest.set_index("sym")["net"].items():
            h = hist.loc[hist["sym"] == sym, "net"].tail(156)
            pctl[sym] = 100.0 * (h <= net).mean()
        return pctl

    def _load_ccy_scores(self) -> dict[str, tuple[float, int]]:
        parts = sorted((self.data_dir.parent / "macro_scores").glob("date=*/part-*.parquet"))
        if not parts:
            self.log.warning("macro_scores absent — composante mac neutre")
            return {}
        df = pd.read_parquet(parts[-1])
        return {r["ccy"]: (float(r["mom"]), int(r["n"])) for _, r in df.iterrows()}

    def collect(self) -> pd.DataFrame:
        return build_rows(self._load_daily(), self._load_cot_pctl(), self.cfg,
                          self.statics, self._load_ccy_scores(), self.mac_cfg)

    def run(self) -> bool:
        try:
            df = self.collect()
            session = pd.Timestamp(df["asof_session"].max()).date()
            self.write_parquet(df, partition_date=str(session))
            self.log.info("run ok", extra={"ctx": {
                "live": int(df["live"].sum()), "session": str(session)}})
            return True
        except Exception:
            self.log.error("run failed — donnée précédente conservée (stale)", exc_info=True)
            return False


if __name__ == "__main__":
    raise SystemExit(0 if TrendBuilder().run() else 1)
