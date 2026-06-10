"""Accès DuckDB de l'API : vues de views.sql sur le data lake parquet.

Connexion in-memory unique au process, vues (re)créées au premier accès ;
les globs parquet sont ré-expansés à chaque requête, donc les nouvelles
partitions écrites par les collecteurs sont visibles sans redémarrage.

DuckDB valide les globs read_parquet à la création de la vue : une vue
dont le dataset n'a pas encore été collecté échoue à la création et est
simplement ignorée (l'endpoint correspondant replie sur la démo). Si la
vue manque encore au moment d'une requête, la connexion est reconstruite
une fois — un dataset collecté après le démarrage de l'API devient ainsi
visible sans redémarrage.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
VIEWS_SQL = Path(__file__).resolve().parent / "views.sql"

log = logging.getLogger("rouge.db")

_lock = threading.Lock()
_con: duckdb.DuckDBPyConnection | None = None


def apply_views(con: duckdb.DuckDBPyConnection, root: Path,
                sql_path: Path = VIEWS_SQL) -> list[str]:
    """Applique views.sql (chemins 'data/…' réécrits sous root), statement
    par statement ; les vues sur datasets absents sont ignorées. Retourne
    la liste des statements ignorés (préfixe de la 1re ligne utile)."""
    sql = sql_path.read_text(encoding="utf-8")
    sql = sql.replace("'data/", f"'{root}/data/")
    skipped: list[str] = []
    for stmt in sql.split(";"):
        if not stmt.strip():
            continue
        try:
            con.execute(stmt)
        except duckdb.Error as err:
            head = next((l.strip() for l in stmt.splitlines()
                         if l.strip() and not l.strip().startswith("--")), "?")
            skipped.append(head)
            log.info("vue ignorée (dataset absent ?) : %s — %s", head[:60], str(err).splitlines()[0])
    return skipped


def _connection() -> duckdb.DuckDBPyConnection:
    global _con
    if _con is None:
        con = duckdb.connect()
        apply_views(con, REPO_ROOT)
        _con = con
    return _con


def query(sql: str, params: list | None = None) -> list[dict[str, Any]]:
    """Exécute et retourne des dicts (connexion partagée, sérialisée).
    Reconstruit la connexion une fois si une vue manque (dataset collecté
    après le démarrage du process)."""
    global _con
    with _lock:
        for attempt in (1, 2):
            try:
                cur = _connection().execute(sql, params or [])
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
            except duckdb.CatalogException:
                if attempt == 2:
                    raise
                _con = None  # vues peut-être créables maintenant — on retente
