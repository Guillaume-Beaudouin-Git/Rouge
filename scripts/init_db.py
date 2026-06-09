"""Initialise data/rouge.duckdb et (ré)applique api/views.sql.

Idempotent : exécutable à tout moment, notamment après chaque ajout
de collecteur en P2. Les vues sur des datasets encore absents sont
simplement ignorées (commentées dans views.sql tant que le parquet
n'existe pas).
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "rouge.duckdb"
VIEWS_SQL = REPO_ROOT / "api" / "views.sql"


def main() -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    sql = VIEWS_SQL.read_text(encoding="utf-8")
    statements = [s.strip() for s in sql.split(";") if s.strip() and not all(
        line.strip().startswith("--") or not line.strip() for line in s.splitlines()
    )]
    for stmt in statements:
        con.execute(stmt)
    tables = con.execute("SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()
    con.close()
    print(f"OK — {DB_PATH.relative_to(REPO_ROOT)} initialisée, {len(tables)} vue(s) : "
          + (", ".join(t[0] for t in tables) or "aucune (normal en P0)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
