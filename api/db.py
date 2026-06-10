"""Accès DuckDB de l'API : vues de views.sql sur le data lake parquet.

Connexion in-memory unique au process, vues (re)créées au premier accès ;
les globs parquet sont ré-expansés à chaque requête, donc les nouvelles
partitions écrites par les collecteurs sont visibles sans redémarrage.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
VIEWS_SQL = Path(__file__).resolve().parent / "views.sql"

_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None


def _connection() -> duckdb.DuckDBPyConnection:
    global _con
    if _con is None:
        con = duckdb.connect()
        sql = VIEWS_SQL.read_text(encoding="utf-8")
        # chemins du repo → absolus (cwd du process indifférent)
        sql = sql.replace("'data/", f"'{REPO_ROOT}/data/")
        for stmt in sql.split(";"):
            if stmt.strip():
                con.execute(stmt)
        _con = con
    return _con


def query(sql: str, params: list | None = None) -> list[dict[str, Any]]:
    """Exécute et retourne des dicts (une connexion partagée, sérialisée)."""
    with _lock:
        cur = _connection().execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
