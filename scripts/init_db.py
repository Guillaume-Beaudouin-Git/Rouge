"""Initialise data/rouge.duckdb et (ré)applique api/views.sql.

Idempotent : exécutable à tout moment, notamment après chaque ajout de
collecteur en P2. Les vues sur des datasets non encore collectés sont
ignorées (DuckDB valide les globs parquet à la création de la vue).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from api.db import apply_views  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "rouge.duckdb"


def main() -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    skipped = apply_views(con, REPO_ROOT)
    views = [v[0] for v in con.execute(
        "SELECT view_name FROM duckdb_views() WHERE NOT internal"
    ).fetchall()]
    con.close()
    print(f"OK — {DB_PATH.relative_to(REPO_ROOT)} : {len(views)} vue(s) : "
          + (", ".join(views) or "aucune"))
    if skipped:
        print(f"ignorées (datasets absents) : {len(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
