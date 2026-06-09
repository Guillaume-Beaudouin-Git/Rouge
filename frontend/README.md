# ROUGE — Terminal d'intelligence marchés

Front-end autonome (un seul fichier, `rouge.html`) : terminal d'intelligence géopolitique et macro-financière en français, conçu comme **outil interne d'équipe** — pas de compte, pas d'abonnement, pas de paiement. Le code, la copy et les données démo de cette base sont originaux ; l'équipe peut y fusionner ses propres contenus et assets.

Deux espaces, deux questions :

- **MONITOR** — *Que se passe-t-il maintenant ?* Carte du monde SVG avec calques de signaux activables.
- **INTEL** — *Que dois-je faire ?* 10 modules d'analyse quantitative (thèses, tendance, COT, macro, FX…).

## Lancer

Ouvrir `rouge.html` dans un navigateur. Aucune dépendance, aucun build, aucun serveur requis. Routeur par hash : `#/`, `#/monitor`, `#/intel/{onglet}`, `#/docs`.

Seule ressource réseau : le flux séismes USGS (live, CORS ouvert) et les polices Google Fonts (fallback système si hors-ligne).

## État des données

| Couche / module | Statut | Source réelle cible | Accès |
|---|---|---|---|
| SEIS (séismes) | **LIVE** | USGS GeoJSON feed `earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson` | Gratuit, CORS ouvert — déjà branché |
| PM (marchés de prédiction) | démo | Polymarket Gamma API (`gamma-api.polymarket.com/markets`) | Gratuit, lecture seule |
| COT (positionnement) | démo | CFTC — rapports COT désagrégés hebdomadaires (CSV/API Socrata `publicreporting.cftc.gov`) | Gratuit |
| MACRO (calendrier, surprises) | démo | TradingEconomics API ou FMP economic calendar | Clé API (free tier limité) |
| NEWS (géolocalisée) | démo | GDELT 2.0 (events + GKG, mise à jour 15 min) | Gratuit |
| AIS (navires) | démo | AISStream.io (WebSocket gratuit) ou Spire/MarineTraffic | Clé gratuite / payant |
| MIL (vols) | démo | OpenSky Network API (filtrage hex codes militaires) | Gratuit (limité) |
| MARKETS (cotations) | démo | Polygon.io, Twelve Data, ou Dukascopy (déjà utilisé dans Algo_claude) | Clé API |
| TREND / TDI / MICRO / SEASON / FX | démo (calculs réels sur graines) | Dérivés des cotations ci-dessus — les formules de score sont déjà dans le code | — |
| ZONES (convergence) | démo | Produit interne : fusion pondérée des autres calques | — |

Les scores composites (TREND = 0.35·momentum + 0.20·macro + 0.15·(positionnement + risque + flux), z-scores TDI, percentiles COT 3 ans, force FX pondérée inverse-vol) sont implémentés dans le JS et fonctionneront tels quels une fois alimentés par de vraies séries.

## Architecture cible (passage en production)

```
collecteurs Python (cron / APScheduler)
  ├─ cot_collector.py        → CFTC Socrata
  ├─ macro_collector.py      → TradingEconomics / FMP
  ├─ news_collector.py       → GDELT
  ├─ pm_collector.py         → Polymarket Gamma
  ├─ ais_ws.py               → AISStream WebSocket
  └─ quotes_collector.py     → Polygon / Dukascopy
        │
        ▼
  Parquet + DuckDB  (data lake local, même pattern que Algo_claude)
        │
        ▼
  FastAPI  (endpoints JSON : /api/trend, /api/cot, /api/monitor/layers…)
        │
        ▼
  rouge.html  (remplacer les constantes démo par des fetch() vers l'API)
```

Chaque bloc de données démo dans le JS est une constante isolée (`THESES`, `COT`, `MACRO_EVENTS`, `FXPAIRS`, `NEWSPTS`, `AIS`, …) : le câblage consiste à remplacer chaque constante par un `fetch('/api/…')` sans toucher aux rendus.

## Déploiement

- **Statique (démo)** : GitHub Pages ou Vercel — pousser `rouge.html` tel quel (renommer `index.html`).
- **Avec backend** : FastAPI sur un VPS (le Hetzner CX22 existant convient), front servi par le même process ou un CDN, reverse proxy Caddy/Nginx.
- **Accès restreint (optionnel)** : l'outil étant interne, un simple Basic Auth au niveau du reverse proxy (Caddy/Nginx) ou un VPN suffit — pas de système de comptes à construire.

## Provenance du code

Cette base est une implémentation écrite de zéro de l'architecture fonctionnelle Monitor/Intel. Toutes les thèses, briefs, chiffres et textes sont des données démo générées par graine aléatoire (mulberry32, seed 20260610) — à remplacer par les contenus réels de l'équipe au moment du câblage.

## Structure du fichier

| Zone | Lignes (approx.) | Contenu |
|---|---|---|
| CSS | 1–230 | Système de design (noir #0A0A0C, os #EAE4D6, rouge signal #FF3526), responsive, reduced-motion |
| HTML | 230–420 | Nav, accueil, monitor, intel, docs, footer |
| JS données | 420–640 | Constantes démo + carte monde inline (177 pays Natural Earth, projection équirectangulaire) |
| JS moteur | 640–904 | Routeur hash, moteur carte (calques, tooltips, fiches pays), rendus des 10 onglets Intel, fetch USGS |
