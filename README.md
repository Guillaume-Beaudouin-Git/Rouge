# ROUGE — terminal d'intelligence marchés (full-stack)

Outil interne d'équipe : front `rouge.html` (SPA single-file, FR) alimenté par
de vraies données via une chaîne **collecteurs Python → Parquet + DuckDB →
API FastAPI → front (fetch)**. Le front, son design et ses rendus ne changent
pas ; seules les constantes démo sont remplacées par des appels API avec
repli sur la démo si l'API ne répond pas.

Documentation du front seul : [`frontend/README.md`](frontend/README.md).

## Structure

```
rouge/
├── frontend/rouge.html          # SPA (MONITOR carte + INTEL 10 vues)
├── collectors/
│   ├── base.py                  # socle : fetch+backoff, cache, parquet, log JSON
│   ├── cot_collector.py         # (P2) CFTC Socrata
│   ├── pm_collector.py          # (P2) Polymarket Gamma
│   ├── news_collector.py        # (P2) GDELT 2.0
│   ├── macro_collector.py       # (P2) TradingEconomics ou FMP
│   ├── flights_collector.py     # (P2) OpenSky
│   ├── ais_ws.py                # (P2) AISStream WebSocket
│   └── quotes_collector.py      # (P2) data lake Dukascopy M5 existant
├── api/
│   ├── main.py                  # (P1) FastAPI + CORS restreint
│   └── views.sql                # vues DuckDB, une par endpoint
├── data/                        # parquet + rouge.duckdb (gitignoré)
├── _logs/                       # logs JSON-lines (gitignoré)
├── scripts/
│   ├── init_db.py               # init DuckDB + (ré)application des vues
│   └── run_all.sh               # orchestration (P3 : APScheduler + systemd)
└── tests/                       # pytest, fixtures enregistrées, zéro réseau en CI
```

## Installation

```bash
python3.12 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env        # puis remplir les clés (voir ci-dessous)
./venv/bin/python scripts/init_db.py
./venv/bin/pytest
```

## Données (parquet partitionné par date)

`data/<dataset>/date=YYYY-MM-DD/part.parquet` — chaque collecteur est
idempotent (ré-exécution = réécriture de la partition du jour). Si un flux
tombe, l'API sert la dernière partition valide avec `stale: true` et le
front garde son fallback démo.

## Contrats API (P1)

```
GET /api/monitor/layers   → news, pm, ais, mil, choke, zones
GET /api/intel/cot        → par contrat : net, pctile_3y, zscore, d_week, sat
GET /api/intel/macro      → événements 14 j : pays, heure, conso, prev, z, hit
GET /api/intel/trend      → 26 actifs : composantes + score + verdict
GET /api/intel/fx         → force G8 inverse-vol + 28 paires + conflits
GET /api/intel/markets    → strip cotations
GET /api/intel/pm         → questions, prix, volume, delta
```

## Clés API (P2)

| Source | Clé | Variable `.env` |
|---|---|---|
| CFTC Socrata, GDELT, Polymarket, USGS | aucune | — |
| TradingEconomics **ou** FMP | free tier | `TRADINGECONOMICS_API_KEY` / `FMP_API_KEY` |
| AISStream.io | gratuite | `AISSTREAM_API_KEY` |
| OpenSky | compte gratuit conseillé | `OPENSKY_CLIENT_ID/SECRET` |
| Dukascopy M5 | data lake local existant | `DUKASCOPY_LAKE_DIR` |

## Phases

- **P0** ✅ scaffold, `base.py`, `.env.example`, front déplacé, DuckDB init
- **P1** API servant les données démo extraites du front, front câblé en
  `fetch()` avec fallback démo — rendu visuel identique
- **P2** collecteurs un par un : COT → Polymarket → macro → GDELT →
  quotes/trend → OpenSky → AIS (chacun avec test + vue DuckDB)
- **P3** APScheduler, systemd (VPS Hetzner), Caddy + Basic Auth, front
  servi statique par FastAPI
