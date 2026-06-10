"""Rétention du data lake — compactage des part-files multiples.

- pm, ais, mil, news_sel, macro_events, macro_scores : les jours PASSÉS
  avec plusieurs part-*.parquet sont compactés en un seul part.parquet
  (le jour courant n'est jamais touché).
- news_raw : compaction mensuelle (mois révolus → un fichier par mois).

Idempotent — planifié quotidiennement par le scheduler.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from collectors.base import REPO_ROOT, get_logger

log = get_logger("retention")

DAILY_COMPACT = ["pm", "ais", "mil", "news_sel", "macro_events", "macro_scores"]
MONTHLY_COMPACT = ["news_raw"]


def _compact_dir(day_dir: Path) -> int:
    parts = sorted(day_dir.glob("part-*.parquet"))
    if len(parts) < 2:
        return 0
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    df.to_parquet(day_dir / "part.parquet", index=False)
    for p in parts:
        p.unlink()
    return len(parts)


def run_retention(today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    stats: dict[str, int] = {}
    for ds in DAILY_COMPACT:
        root = REPO_ROOT / "data" / ds
        n = 0
        for day_dir in sorted(root.glob("date=*")) if root.exists() else []:
            if day_dir.name.removeprefix("date=") >= str(today):
                continue  # jamais le jour courant
            n += _compact_dir(day_dir)
        if n:
            stats[ds] = n
    for ds in MONTHLY_COMPACT:
        root = REPO_ROOT / "data" / ds
        cur_month = str(today)[:7]
        by_month: dict[str, list[Path]] = {}
        for day_dir in sorted(root.glob("date=*")) if root.exists() else []:
            month = day_dir.name.removeprefix("date=")[:7]
            if month < cur_month:
                by_month.setdefault(month, []).extend(day_dir.glob("*.parquet"))
        n = 0
        for month, files in by_month.items():
            if len(files) < 2:
                continue
            df = pd.concat([pd.read_parquet(p) for p in files], ignore_index=True)
            out_dir = root / f"date={month}-01"
            out_dir.mkdir(exist_ok=True)
            df.to_parquet(out_dir / "part.parquet", index=False)
            for p in files:
                if p != out_dir / "part.parquet":
                    p.unlink()
            for day_dir in root.glob(f"date={month}-*"):
                if day_dir.is_dir() and not any(day_dir.iterdir()):
                    day_dir.rmdir()
            n += len(files)
        if n:
            stats[ds] = n
    log.info("retention ok", extra={"ctx": {"compactes": stats or "rien à faire"}})
    return stats


if __name__ == "__main__":
    run_retention()
