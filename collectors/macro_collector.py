"""Collecteur macro — FairEconomy (miroir JSON ForexFactory), option 2.

Décision du 2026-06-10 : FMP a verrouillé le calendrier éco (HTTP 402/403
avec clé free) et TradingEconomics a supprimé le compte invité → bascule
FairEconomy (gratuit, sans clé). Limites assumées :
- fenêtre = semaine courante uniquement (ff_calendar_nextweek.json : 404),
- AUCUN champ actual dans le feed : le réalisé d'un événement est dérivé
  du champ `previous` de l'occurrence SUIVANTE de la même série.

Règles de vintage (committées avant construction) :
- actual_first_seen est GELÉ à la première observation, jamais écrasé ;
  les révisions vont dans actual_revised + revised_at ;
- beatZ/missZ/hit se calculent exclusivement sur actual_first_seen vs
  consensus — la surprise qui compte est celle du premier print ;
- le consensus d'un événement reste actualisable jusqu'à son heure de
  publication, puis est gelé.

Le hit-rate se remplit progressivement au fil des collectes (cf. README).
Cadence : 1 pull/jour + re-pull à J+1 (assuré par la boucle de collecte).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from collectors.base import REPO_ROOT, BaseCollector

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
VINTAGE_PATH = REPO_ROOT / "data" / "macro_vintage" / "vintage.parquet"

#: devises du front → iso drapeau (contrat CCYS de rouge.html)
CCY_ISO = {"USD": "US", "EUR": "EU", "GBP": "GB", "JPY": "JP",
           "AUD": "AU", "CAD": "CA", "CHF": "CH", "CNY": "CN"}
TIER = {"High": 3, "Medium": 2, "Low": 1}
#: minimum de surprises d'une série pour calculer ses z
MIN_OBS = 3
#: fenêtre servie (bornée par le feed : semaine courante)
WINDOW_DAYS = 14


def parse_num(raw) -> float | None:
    """'5.6%', '-0.1%', '3.26T', '62.4', '' → float du préfixe numérique
    (unité ignorée : cohérente au sein d'une même série)."""
    if raw is None:
        return None
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(raw))
    return float(m.group(1)) if m else None


def event_uid(ccy: str, title: str, dt: datetime) -> str:
    return hashlib.sha1(f"{ccy}|{title}|{dt.isoformat()}".encode()).hexdigest()[:16]


def parse_feed(rows: list[dict], now: datetime) -> pd.DataFrame:
    """Feed brut → événements normalisés (devises du front uniquement)."""
    out = []
    for r in rows:
        ccy = r.get("country")
        tier = TIER.get(r.get("impact"))
        if ccy not in CCY_ISO or tier is None:
            continue
        dt = datetime.fromisoformat(r["date"]).astimezone(timezone.utc)
        out.append({
            "uid": event_uid(ccy, r["title"], dt),
            "series": f"{ccy}|{r['title']}",
            "ccy": ccy, "iso": CCY_ISO[ccy], "name": r["title"],
            "dt": dt, "tier": tier,
            "cons": parse_num(r.get("forecast")),
            "prev": parse_num(r.get("previous")),
        })
    return pd.DataFrame(out)


VINTAGE_COLS = ["uid", "series", "ccy", "iso", "name", "dt", "tier", "cons",
                "prev", "first_seen_at", "actual_first_seen",
                "actual_seen_at", "actual_revised", "revised_at"]


def update_vintage(vintage: pd.DataFrame, events: pd.DataFrame,
                   now: datetime) -> pd.DataFrame:
    """Fusionne un snapshot du feed dans le store vintage (règles de gel)."""
    if vintage.empty:
        vintage = pd.DataFrame(columns=VINTAGE_COLS)
    known = set(vintage["uid"])
    new = events[~events["uid"].isin(known)].copy()
    if len(new):
        new["first_seen_at"] = now
        for c in ("actual_first_seen", "actual_seen_at", "actual_revised", "revised_at"):
            new[c] = None
        vintage = pd.concat([vintage, new[VINTAGE_COLS]], ignore_index=True)
    # consensus/previous actualisables jusqu'à la publication, gelés après
    v = vintage.set_index("uid")
    for _, e in events[events["uid"].isin(known)].iterrows():
        if now < v.at[e["uid"], "dt"]:
            v.at[e["uid"], "cons"] = e["cons"]
            v.at[e["uid"], "prev"] = e["prev"]
    # dérivation des actuals : previous de l'occurrence suivante de la série
    v = v.reset_index()
    v = v.sort_values(["series", "dt"]).reset_index(drop=True)
    for i in range(len(v) - 1):
        cur, nxt = v.loc[i], v.loc[i + 1]
        if cur["series"] != nxt["series"] or nxt["prev"] is None or pd.isna(nxt["prev"]):
            continue
        if cur["actual_first_seen"] is None or pd.isna(cur["actual_first_seen"]):
            v.loc[i, "actual_first_seen"] = nxt["prev"]      # GELÉ désormais
            v.loc[i, "actual_seen_at"] = now
        elif float(nxt["prev"]) != float(cur["actual_first_seen"]):
            v.loc[i, "actual_revised"] = nxt["prev"]          # révision à part
            v.loc[i, "revised_at"] = now
    return v


def series_stats(vintage: pd.DataFrame) -> pd.DataFrame:
    """Surprises par série depuis actual_first_seen vs consensus (premier
    print uniquement). → une ligne par surprise : series, ccy, dt, z."""
    ok = vintage.dropna(subset=["actual_first_seen", "cons"]).copy()
    if ok.empty:
        return pd.DataFrame(columns=["series", "ccy", "name", "dt", "z"])
    ok["surprise"] = ok["actual_first_seen"].astype(float) - ok["cons"].astype(float)
    rows = []
    for series, g in ok.groupby("series"):
        if len(g) < MIN_OBS:
            continue
        sd = g["surprise"].std(ddof=1)
        if not sd or np.isnan(sd) or sd == 0:
            continue
        for _, r in g.iterrows():
            rows.append({"series": series, "ccy": r["ccy"], "name": r["name"],
                         "dt": r["dt"], "z": float(r["surprise"] / sd)})
    return pd.DataFrame(rows)


def build_serve(vintage: pd.DataFrame, now: datetime) -> tuple[pd.DataFrame, pd.DataFrame]:
    """→ (events fenêtre à venir — forme front, scores par devise)."""
    vintage = vintage.copy()
    vintage["dt"] = pd.to_datetime(vintage["dt"], utc=True)
    zdf = series_stats(vintage)
    by_series: dict[str, pd.DataFrame] = dict(tuple(zdf.groupby("series"))) if not zdf.empty else {}

    today = now.date()
    horizon = today + timedelta(days=WINDOW_DAYS)
    up = vintage[(vintage["dt"].dt.date >= today) & (vintage["dt"].dt.date <= horizon)]
    ev_rows = []
    for _, e in up.sort_values("dt").iterrows():
        s = by_series.get(e["series"])
        pos = s[s["z"] > 0]["z"] if s is not None else pd.Series(dtype=float)
        neg = s[s["z"] < 0]["z"] if s is not None else pd.Series(dtype=float)
        ev_rows.append({
            "d": (e["dt"].date() - today).days,
            "dt": e["dt"].isoformat(),
            "ccy": e["ccy"], "iso": e["iso"], "name": e["name"],
            "tier": int(e["tier"]), "time": e["dt"].strftime("%H:%M"),
            "prev": e["prev"], "cons": e["cons"],
            "beatZ": round(float(pos.mean()), 2) if len(pos) else 0.0,
            "missZ": round(float(neg.mean()), 2) if len(neg) else 0.0,
            "hit": int(round(100 * (s["z"] > 0).mean())) if s is not None else 0,
            "n": int(len(s)) if s is not None else 0,
        })
    events = pd.DataFrame(ev_rows)

    sc_rows = []
    for ccy, iso in CCY_ISO.items():
        zc = zdf[zdf["ccy"] == ccy].sort_values("dt") if not zdf.empty else pd.DataFrame()
        pos = zc[zc["z"] > 0]["z"] if len(zc) else pd.Series(dtype=float)
        neg = zc[zc["z"] < 0]["z"] if len(zc) else pd.Series(dtype=float)
        streak = 0
        for z in reversed(list(zc["z"])) if len(zc) else []:
            sgn = 1 if z > 0 else -1
            if streak == 0:
                streak = sgn
            elif np.sign(streak) == sgn:
                streak += sgn
            else:
                break
        top = "—"
        if len(zc):
            m = zc.groupby("name")["z"].mean().abs().sort_values(ascending=False)
            top = m.index[0]
        sc_rows.append({
            "ccy": ccy, "iso": iso,
            "next": int((events["ccy"] == ccy).sum()) if len(events) else 0,
            "beatZ": round(float(pos.mean()), 2) if len(pos) else 0.0,
            "missZ": round(float(neg.mean()), 2) if len(neg) else 0.0,
            "beatPct": int(round(100 * (zc["z"] > 0).mean())) if len(zc) else 0,
            "n": int(len(zc)),
            "mom": round(float(zc["z"].tail(5).mean()), 2) if len(zc) else 0.0,
            "streak": int(streak),
            "top": top,
        })
    return events, pd.DataFrame(sc_rows)


class MacroCollector(BaseCollector):
    name = "macro"
    dataset = "macro_events"
    cache_ttl = 3600  # cadence quotidienne — absorbe les doubles pulls

    def collect(self) -> pd.DataFrame:
        now = datetime.now(timezone.utc)
        feed = self.fetch_json(FEED_URL)
        events = parse_feed(feed, now)
        if events.empty:
            raise RuntimeError("feed FairEconomy vide après filtrage G8")
        vintage = (pd.read_parquet(VINTAGE_PATH)
                   if VINTAGE_PATH.exists() else pd.DataFrame())
        if not vintage.empty:
            vintage["dt"] = pd.to_datetime(vintage["dt"], utc=True)
        vintage = update_vintage(vintage, events, now)
        VINTAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        vintage.to_parquet(VINTAGE_PATH, index=False)
        serve_events, scores = build_serve(vintage, now)
        self._scores = scores
        self._now = now
        n_actuals = int(vintage["actual_first_seen"].notna().sum())
        self.log.info("vintage", extra={"ctx": {
            "events_store": len(vintage), "actuals_geles": n_actuals,
            "surprises_z": int(scores["n"].sum())}})
        return serve_events

    def run(self) -> bool:
        try:
            events = self.collect()
            now = self._now
            for df, ds in ((events, "macro_events"), (self._scores, "macro_scores")):
                df = df.copy()
                df["snapshot_ts"] = now
                part_dir = REPO_ROOT / "data" / ds / f"date={now:%Y-%m-%d}"
                part_dir.mkdir(parents=True, exist_ok=True)
                df.to_parquet(part_dir / f"part-{now:%H%M%S}.parquet", index=False)
            self.log.info("run ok", extra={"ctx": {
                "events_servis": len(events),
                "fenetre_max_j": int(events["d"].max()) if len(events) else 0}})
            return True
        except Exception:
            self.log.error("run failed — donnée précédente conservée (stale)", exc_info=True)
            return False
        finally:
            if self._client is not None:
                self._client.close()
                self._client = None


if __name__ == "__main__":
    raise SystemExit(0 if MacroCollector().run() else 1)
