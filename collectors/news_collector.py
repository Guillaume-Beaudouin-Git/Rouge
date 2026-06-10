"""Collecteur NEWS — GDELT GKG 2.1, fichiers 15 min (décision documentée
dans docs/decisions/gdelt_source.md : GEO API morte, DOC API rate-limitée
et sans coordonnées).

Chaque collecte ingère le dernier fichier GKG (cache 15 min aligné sur la
cadence GDELT), filtre par thèmes (news_map.yaml), écrit le brut, puis
recalcule la sélection servie : fenêtre 24 h, dédup URL canonique,
clustering <50 km par catégorie, cap 18 points, score récence × volume.
"""

from __future__ import annotations

import csv
import io
import math
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import yaml

from collectors.base import REPO_ROOT, BaseCollector

MAP_PATH = REPO_ROOT / "api" / "config" / "news_map.yaml"
LASTUPDATE_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"

#: indices de champs GKG 2.1 (27 colonnes — cf. note de décision)
F_DATE, F_SOURCE, F_URL, F_THEMES, F_LOCS, F_XTRAS = 1, 3, 4, 7, 9, 26

_TITLE_RE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)


def load_news_map(path: Path = MAP_PATH) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not cfg.get("categories"):
        raise RuntimeError("news_map.yaml : aucune catégorie définie")
    for key, cat in cfg["categories"].items():
        if not cat.get("themes"):
            raise RuntimeError(f"news_map.yaml : thèmes manquants pour '{key}'")
    for field in ("window_hours", "cluster_km", "serve_max", "recency_halflife_hours"):
        if field not in cfg:
            raise RuntimeError(f"news_map.yaml : champ '{field}' manquant")
    return cfg


def canonical_url(url: str) -> str:
    """URL canonique pour la dédup : host minuscule sans www, chemin sans
    slash final, querystring et fragment ignorés."""
    p = urlparse(url.strip())
    host = p.netloc.lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/')}"


def slug_title(url: str) -> str:
    """Titre de repli depuis le slug d'URL."""
    seg = [s for s in urlparse(url).path.split("/") if s]
    if not seg:
        return urlparse(url).netloc
    return re.sub(r"[-_]+", " ", re.sub(r"\.\w{2,5}$", "", seg[-1])).strip().capitalize()


def best_location(v1locations: str) -> tuple[float, float] | None:
    """V1Locations → (lat, lon) : entrée la plus spécifique (ville > pays)."""
    best, best_type = None, -1
    for entry in v1locations.split(";"):
        parts = entry.split("#")
        if len(parts) < 6:
            continue
        try:
            typ, lat, lon = int(parts[0]), float(parts[4]), float(parts[5])
        except ValueError:
            continue
        # types GKG : 3/4 = villes, 2/5 = régions, 1 = pays
        rank = {4: 3, 3: 3, 5: 2, 2: 2, 1: 1}.get(typ, 0)
        if rank > best_type:
            best, best_type = (lat, lon), rank
    return best


def parse_gkg(raw: str, cfg: dict) -> pd.DataFrame:
    """CSV GKG (latin-1, tab) → points normalisés, un par article matché."""
    theme_to_cat = {t: key for key, cat in cfg["categories"].items() for t in cat["themes"]}
    rows = []
    for r in csv.reader(io.StringIO(raw), delimiter="\t"):
        if len(r) < 27:
            continue
        cats = {theme_to_cat[t] for t in r[F_THEMES].split(";") if t in theme_to_cat}
        if not cats:
            continue
        loc = best_location(r[F_LOCS])
        if loc is None:
            continue
        m = _TITLE_RE.search(r[F_XTRAS])
        title = m.group(1).strip() if m and m.group(1).strip() else slug_title(r[F_URL])
        rows.append({
            "ts": datetime.strptime(r[F_DATE], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc),
            "source": r[F_SOURCE],
            "url": r[F_URL],
            "url_canon": canonical_url(r[F_URL]),
            "title": title[:160],
            "lat": loc[0],
            "lon": loc[1],
            "category": sorted(cats)[0],
            "n_themes": len(cats),
        })
    return pd.DataFrame(rows)


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    rl1, rl2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = rl2 - rl1, math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rl1) * math.cos(rl2) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def select_points(df: pd.DataFrame, cfg: dict,
                  now: datetime | None = None) -> pd.DataFrame:
    """Fenêtre 24 h → dédup URL canonique → clusters <cluster_km même
    catégorie → score volume × récence → top serve_max.

    Sortie (un point par cluster) : lon, lat, w (1-3), title, source, ts,
    category, n (articles du cluster)."""
    now = now or datetime.now(timezone.utc)
    cut = now - timedelta(hours=cfg["window_hours"])
    df = df[df["ts"] >= cut]
    # dédup URL canonique : on garde l'observation la plus récente
    df = (df.sort_values("ts")
            .drop_duplicates(subset="url_canon", keep="last"))
    if df.empty:
        return pd.DataFrame(columns=["lon", "lat", "w", "title", "source",
                                     "ts", "category", "n"])
    # clustering glouton par catégorie, articles les plus récents d'abord
    clusters: list[dict] = []
    for _, r in df.sort_values("ts", ascending=False).iterrows():
        for c in clusters:
            if c["category"] == r["category"] and \
               _haversine_km(r["lat"], r["lon"], c["lat"], c["lon"]) < cfg["cluster_km"]:
                c["n"] += 1
                break
        else:
            clusters.append({"lat": r["lat"], "lon": r["lon"], "title": r["title"],
                             "source": r["source"], "ts": r["ts"],
                             "category": r["category"], "n": 1})
    half = cfg["recency_halflife_hours"]
    for c in clusters:
        age_h = (now - c["ts"]).total_seconds() / 3600
        c["score"] = c["n"] * math.exp(-age_h / half)
        c["w"] = 3 if c["n"] >= 5 else 2 if c["n"] >= 2 else 1
    ranked = pd.DataFrame(clusters).sort_values("score", ascending=False)
    # cap global + cap par catégorie : préserve la diversité des sujets
    # face aux catégories à très gros volume (élections US, mesuré)
    cat_max = cfg.get("category_max", cfg["serve_max"])
    kept, counts = [], {}
    for _, r in ranked.iterrows():
        if len(kept) >= cfg["serve_max"]:
            break
        if counts.get(r["category"], 0) >= cat_max:
            continue
        counts[r["category"]] = counts.get(r["category"], 0) + 1
        kept.append(r)
    out = pd.DataFrame(kept).reset_index(drop=True)
    return out[["lon", "lat", "w", "title", "source", "ts", "category", "n"]]


class NewsCollector(BaseCollector):
    name = "news"
    dataset = "news_raw"
    cache_ttl = 900   # cadence de publication GDELT : 15 min
    timeout = 20.0    # l'API rend des 504 sous charge — timeout court,
    max_retries = 3   # backoff de base.py absorbe, stale sert le reste

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_news_map()

    def _latest_gkg_url(self) -> str:
        txt = self.fetch(LASTUPDATE_URL).text
        for line in txt.splitlines():
            if ".gkg.csv.zip" in line:
                return line.split()[-1]
        raise RuntimeError("lastupdate.txt : pas de ligne gkg")

    def collect(self) -> pd.DataFrame:
        url = self._latest_gkg_url()
        stamp = re.search(r"(\d{14})\.gkg", url).group(1)
        marker = self.data_dir / f"date={stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" / f"part-{stamp[8:]}.parquet"
        if marker.exists():
            self.log.info("fichier déjà ingéré", extra={"ctx": {"stamp": stamp}})
            return pd.DataFrame()
        resp = self.fetch(url, use_cache=False)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            raw = z.read(z.namelist()[0]).decode("latin-1")
        df = parse_gkg(raw, self.cfg)
        df["gkg_stamp"] = stamp
        self.log.info("gkg parsé", extra={"ctx": {"stamp": stamp, "points": len(df)}})
        return df

    def _load_window(self, now: datetime) -> pd.DataFrame:
        cut = now - timedelta(hours=self.cfg["window_hours"])
        frames = []
        for p in sorted(self.data_dir.glob("date=*/part-*.parquet")):
            day = p.parent.name.removeprefix("date=")
            if day >= cut.strftime("%Y-%m-%d"):
                frames.append(pd.read_parquet(p))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def run(self) -> bool:
        try:
            df = self.collect()
            now = datetime.now(timezone.utc)
            if not df.empty:
                stamp = df["gkg_stamp"].iloc[0]
                part_dir = self.data_dir / f"date={stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"
                part_dir.mkdir(parents=True, exist_ok=True)
                df.to_parquet(part_dir / f"part-{stamp[8:]}.parquet", index=False)
            sel = select_points(self._load_window(now), self.cfg, now=now)
            if sel.empty:
                raise RuntimeError("sélection vide — fenêtre 24 h sans point")
            sel["snapshot_ts"] = now
            sel_dir = REPO_ROOT / "data" / "news_sel" / f"date={now:%Y-%m-%d}"
            sel_dir.mkdir(parents=True, exist_ok=True)
            sel.to_parquet(sel_dir / f"part-{now:%H%M%S}.parquet", index=False)
            self.log.info("run ok", extra={"ctx": {
                "raw_points": len(df), "servis": len(sel),
                "categories": sel["category"].value_counts().to_dict()}})
            return True
        except Exception:
            self.log.error("run failed — donnée précédente conservée (stale)", exc_info=True)
            return False
        finally:
            if self._client is not None:
                self._client.close()
                self._client = None


if __name__ == "__main__":
    raise SystemExit(0 if NewsCollector().run() else 1)
