"""Collecteur quotes — data lake Dukascopy d'Algo_claude, LECTURE SEULE.

Aucune écriture, aucun fichier temporaire dans Algo_claude : toutes les
sorties vont dans data/quotes_daily/ de rouge.

Agrégation M5 → daily, convention unique testée (cf. quotes_map.yaml) :
close de session = dernière barre démarrant AVANT 17:00 America/New_York ;
la session J couvre [J-1 17:00, J 17:00) en heure de New York. Pas de
minuit UTC implicite. Convention identique FX / métaux / indices / énergies.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from collectors.base import REPO_ROOT, BaseCollector

MAP_PATH = REPO_ROOT / "api" / "config" / "quotes_map.yaml"

#: profondeur chargée : historique COMPLET du lake — nécessaire à la
#: saisonnalité (FX ~19 ans) ; trend/fx font leur .tail() eux-mêmes
FULL_SINCE = date(2003, 1, 1)


def load_quotes_map(path: Path = MAP_PATH) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    m5 = os.getenv(cfg["lake"]["m5_env"], "")
    m1 = os.getenv(cfg["lake"]["m1_env"], "")
    if not m5 or not Path(m5).exists():
        raise RuntimeError(f"lake M5 introuvable — renseigner {cfg['lake']['m5_env']} dans .env")
    cfg["m5_dir"] = Path(m5)
    cfg["m1_dir"] = Path(m1) if m1 else None
    return cfg


def sessionize(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Timestamps de début de barre (tz-aware) → date de session NY.

    Décalage +7 h sur l'heure de New York : une barre démarrant à 17:00 NY
    ou après appartient à la session du lendemain.
    """
    ny = index.tz_convert("America/New_York")
    return (ny + pd.Timedelta(hours=7)).normalize().tz_localize(None)


def m5_to_daily(close: pd.Series) -> pd.DataFrame:
    """Série M5 close (index tz-aware) → barres daily (close 17:00 NY)."""
    if close.index.tz is None:
        raise ValueError("index naïf : le contrat de schéma exige un index tz-aware")
    sessions = sessionize(close.index)
    daily = close.groupby(sessions).last().to_frame("close")
    daily.index.name = "session"
    # les week-ends produisent des sessions quasi vides (qq barres du
    # dimanche soir) : on ne garde que les sessions de semaine
    daily = daily[daily.index.dayofweek < 5]
    # session finale incomplète (flux arrêté avant la clôture : ex. barres
    # du dimanche soir rattachées au lundi) → droppée, pas de close partiel
    last_bar_ny = close.index.max().tz_convert("America/New_York")
    session_close = (pd.Timestamp(daily.index[-1], tz="America/New_York")
                     + pd.Timedelta(hours=17))
    if last_bar_ny < session_close - pd.Timedelta(hours=2):
        daily = daily.iloc[:-1]
    daily["ret"] = daily["close"].pct_change()
    return daily


class QuotesCollector(BaseCollector):
    name = "quotes"
    dataset = "quotes_daily"

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_quotes_map()

    # --------------------------------------------------------- lecture lake

    def _load_m5(self, file: str, since: date) -> pd.Series:
        path = self.cfg["m5_dir"] / f"{file}_m5_close.parquet"
        df = pd.read_parquet(path)  # lecture seule
        s = df["close"]
        return s[s.index >= pd.Timestamp(since, tz=s.index.tz)]

    def _load_m1_yearly(self, file: str, since: date) -> pd.Series:
        m1_dir = self.cfg["m1_dir"]
        if m1_dir is None or not m1_dir.exists():
            raise RuntimeError("DUKASCOPY_M1_DIR non renseigné")
        frames = []
        for year in range(since.year, date.today().year + 1):
            p = m1_dir / f"{file}_{year}.parquet"
            if p.exists():
                frames.append(pd.read_parquet(p, columns=["ts_utc", "close"]))
        if not frames:
            raise RuntimeError(f"aucun fichier {file}_YYYY.parquet dans {m1_dir}")
        df = pd.concat(frames, ignore_index=True).sort_values("ts_utc")
        s = pd.Series(df["close"].values,
                      index=pd.DatetimeIndex(df["ts_utc"]).tz_localize("UTC"))
        return s[s.index >= pd.Timestamp(since, tz="UTC")]

    # -------------------------------------------------------------- collect

    def collect(self) -> pd.DataFrame:
        since = FULL_SINCE
        frames = []
        for inst in self.cfg["instruments"]:
            if "excluded" in inst:
                continue
            loader = self._load_m5 if inst["source"] == "m5" else self._load_m1_yearly
            daily = m5_to_daily(loader(inst["file"], since))
            daily["sym"] = inst["sym"]
            frames.append(daily.reset_index())
            self.log.info("daily ok", extra={"ctx": {
                "sym": inst["sym"], "sessions": len(daily),
                "last": str(daily.index.max().date()) if len(daily) else None}})
        return pd.concat(frames, ignore_index=True)

    def run(self) -> bool:
        """Une partition par instrument (réécrite intégralement : idempotent)."""
        try:
            df = self.collect()
            for sym, group in df.groupby("sym"):
                part_dir = self.data_dir / f"sym={sym}"
                part_dir.mkdir(parents=True, exist_ok=True)
                group.drop(columns="sym").to_parquet(part_dir / "part.parquet", index=False)
            self.log.info("run ok", extra={"ctx": {
                "instruments": df["sym"].nunique(), "rows": len(df),
                "last_session": str(df["session"].max().date())}})
            return True
        except Exception:
            self.log.error("run failed — donnée précédente conservée (stale)", exc_info=True)
            return False


if __name__ == "__main__":
    raise SystemExit(0 if QuotesCollector().run() else 1)
