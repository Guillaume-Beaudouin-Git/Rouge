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
│   ├── main.py                  # FastAPI : front statique + /api/* (enveloppe data/meta)
│   ├── demo/*.json              # fixtures extraites du front (source de vérité unique)
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

## Score TREND : composantes partielles

`g = 0.35·mom + 0.20·mac + 0.15·(pos + risk + flow)`. Tant que les
collecteurs macro (`mac`) et flux (`flow`) ne sont pas branchés, ces
composantes valent 0 **sans renormalisation** : le score brut est inchangé
et les verdicts (seuil ±18) sont mécaniquement conservateurs.
`meta.effective_weight` expose la part du poids total portée par des
composantes live (0.65 aujourd'hui : mom+pos+risk) — elle remontera à 1.0
au fil des branchements. `meta.components`, `meta.excluded` et
`meta.pos_missing` détaillent l'état par composante et par actif.
Backlog des actifs/alimentations manquants : [BACKLOG.md](BACKLOG.md).

## Macro : FairEconomy et hit-rate progressif

Choix de provider tranché empiriquement (2026-06-10) : TradingEconomics a
supprimé le compte invité (HTTP 410) et FMP a verrouillé le calendrier
derrière le payant même avec clé (402/403) → **FairEconomy** (miroir JSON
ForexFactory, gratuit, sans clé). Deux limites assumées :

- **fenêtre = semaine courante** (le feed nextweek n'existe pas) : la vue
  affiche moins que les 14 jours du design, `meta.window_days` fait foi ;
- **pas de champ actual** : le réalisé d'un événement est dérivé du
  `previous` de l'occurrence suivante de la même série. Règle de vintage :
  `actual_first_seen` est gelé à la première observation (les révisions
  vont dans `actual_revised`), et beatZ/missZ/hit se calculent
  exclusivement sur le premier print vs consensus. Conséquence : le
  hit-rate **se remplit progressivement au fil des collectes**
  (`meta.history_n` compte les surprises accumulées — 0 au premier jour,
  les séries mensuelles mettent ~2 mois à produire leurs premiers z).

## Microstructure : heures UTC et DST

La heatmap de vol horaire est étiquetée en **UTC fixe**. Les sessions de
marché locales (ouverture US 9:30 New York, fixing de Londres…) glissent
d'une heure entre été et hiver par rapport à cette grille : un même
créneau UTC peut couvrir deux régimes selon la saison. Le percentile par
heure (fenêtre 1 an) lisse ce glissement mais ne le supprime pas — lire
les heures frontières (12-14 UTC, 20-22 UTC) avec cette réserve.

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
- **P1** ✅ API servant les fixtures démo extraites du front
  (`scripts/extract_demo.py` → `api/demo/*.json`), front servi same-origin
  sur `http://localhost:8000`, câblé en `loadData()` (timeout 2,5 s) avec
  fallback démo et badges LIVE/STALE/DÉMO — rendu visuel identique.
  Dev : `./venv/bin/uvicorn api.main:app --reload` puis ouvrir
  localhost:8000 (ne pas ouvrir en `file://`).
- **P2** collecteurs un par un : COT → Polymarket → macro → GDELT →
  quotes/trend → OpenSky → AIS (chacun avec test + vue DuckDB)
- **P3** APScheduler, systemd (VPS Hetzner), Caddy + Basic Auth, front
  servi statique par FastAPI
