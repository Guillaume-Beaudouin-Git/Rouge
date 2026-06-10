"""Collecteur COT — CFTC via Socrata (publicreporting.cftc.gov).

Deux datasets : Disaggregated futures-only (commodités, Managed Money) et
TFF futures-only (financiers, Leveraged Funds). Le mapping symbole front →
contrat est dans api/config/cot_map.yaml — rien en dur ici.

Backfill ≥ 3 ans au premier run (percentiles/z-scores calculables
immédiatement), puis incrémental avec fenêtre de révision. Une partition
parquet par date de rapport (le mardi de référence) : idempotent.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from collectors.base import REPO_ROOT, BaseCollector

MAP_PATH = REPO_ROOT / "api" / "config" / "cot_map.yaml"
FRONT_FIXTURE = REPO_ROOT / "api" / "demo" / "cot.json"

SOCRATA_BASE = "https://publicreporting.cftc.gov/resource"
PAGE_SIZE = 20_000
#: ~170 semaines — marge au-dessus des 156 nécessaires au percentile 3 ans
BACKFILL_DAYS = 1190
#: refenêtrage incrémental : les rapports peuvent être révisés
REVISION_DAYS = 35


def load_cot_map(path: Path = MAP_PATH) -> dict:
    """Charge et valide le mapping. Échec explicite si un des contrats du
    front (api/demo/cot.json fait foi) n'est pas mappé."""
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    mapped = {c["sym"] for c in cfg["contracts"]}
    front = {row["name"] for row in json.loads(FRONT_FIXTURE.read_text(encoding="utf-8"))}
    missing = front - mapped
    if missing:
        raise RuntimeError(
            f"cot_map.yaml incomplet — contrats du front non mappés : {sorted(missing)}"
        )
    unknown_ds = {c["dataset"] for c in cfg["contracts"]} - set(cfg["datasets"])
    if unknown_ds:
        raise RuntimeError(f"cot_map.yaml : datasets inconnus référencés : {sorted(unknown_ds)}")
    return cfg


def normalize(rows: list[dict], cfg: dict, dataset: str) -> pd.DataFrame:
    """Réponse Socrata → lignes normalisées (une par contrat × semaine)."""
    ds = cfg["datasets"][dataset]
    by_code = {c["code"]: (i, c) for i, c in enumerate(cfg["contracts"]) if c["dataset"] == dataset}
    out = []
    for r in rows:
        code = r["cftc_contract_market_code"]
        if code not in by_code:
            continue
        ord_, c = by_code[code]
        long_ = int(r[ds["long_col"]])
        short = int(r[ds["short_col"]])
        out.append({
            "sym": c["sym"],
            "iso": c["iso"],
            "ord": ord_,
            "dataset": dataset,
            "code": code,
            "category": c["category"],
            "report_date": datetime.fromisoformat(r["report_date_as_yyyy_mm_dd"]).date(),
            "long": long_,
            "short": short,
            "net": long_ - short,
            "open_interest": int(r.get("open_interest_all") or 0),
        })
    return pd.DataFrame(out)


class CotCollector(BaseCollector):
    name = "cot"
    dataset = "cot"
    timeout = 60.0

    def __init__(self) -> None:
        super().__init__()
        self.cfg = load_cot_map()
        self.headers = {}
        token = os.getenv("SOCRATA_APP_TOKEN", "").strip()
        if token:
            self.headers["X-App-Token"] = token

    # ------------------------------------------------------------ fetch

    def _start_date(self) -> date:
        last = self.latest_partition()
        if last is None:
            return date.today() - timedelta(days=BACKFILL_DAYS)
        last_date = date.fromisoformat(last.parent.name.removeprefix("date="))
        return last_date - timedelta(days=REVISION_DAYS)

    def _fetch_dataset(self, dataset: str, since: date) -> list[dict]:
        ds = self.cfg["datasets"][dataset]
        codes = ",".join(
            f"'{c['code']}'" for c in self.cfg["contracts"] if c["dataset"] == dataset
        )
        select = ",".join([
            "report_date_as_yyyy_mm_dd", "cftc_contract_market_code",
            "contract_market_name", ds["long_col"], ds["short_col"], "open_interest_all",
        ])
        where = (
            f"cftc_contract_market_code in({codes}) "
            f"AND report_date_as_yyyy_mm_dd >= '{since.isoformat()}T00:00:00.000'"
        )
        rows: list[dict] = []
        offset = 0
        while True:
            page = self.fetch_json(
                f"{SOCRATA_BASE}/{ds['id']}.json",
                params={"$select": select, "$where": where,
                        "$order": "report_date_as_yyyy_mm_dd", "$limit": PAGE_SIZE,
                        "$offset": offset},
                headers=self.headers,
            )
            rows.extend(page)
            if len(page) < PAGE_SIZE:
                return rows
            offset += PAGE_SIZE

    # ---------------------------------------------------------- collect

    def collect(self) -> pd.DataFrame:
        since = self._start_date()
        self.log.info("fetch COT", extra={"ctx": {"since": since.isoformat()}})
        frames = [
            normalize(self._fetch_dataset(ds, since), self.cfg, ds)
            for ds in self.cfg["datasets"]
        ]
        df = pd.concat([f for f in frames if not f.empty], ignore_index=True)
        if df.empty:
            raise RuntimeError(f"Socrata n'a renvoyé aucune ligne depuis {since}")
        return df

    def run(self) -> bool:
        """Une partition parquet PAR DATE DE RAPPORT (pas par date de
        collecte) : le ré-import d'une semaine révisée réécrit sa partition."""
        try:
            df = self.collect()
            for report_date, group in df.groupby("report_date"):
                self.write_parquet(group.reset_index(drop=True), partition_date=str(report_date))
            self.log.info("run ok", extra={"ctx": {
                "rows": len(df), "weeks": df["report_date"].nunique(),
                "last_report": str(df["report_date"].max())}})
            return True
        except Exception:
            self.log.error("run failed — donnée précédente conservée (stale)", exc_info=True)
            return False
        finally:
            if self._client is not None:
                self._client.close()
                self._client = None


if __name__ == "__main__":
    raise SystemExit(0 if CotCollector().run() else 1)
