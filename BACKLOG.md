# BACKLOG — hors périmètre P2, tracé

## TREND : actifs exclus du lake Dukascopy (19/26 live)

| Actifs | Voie d'alimentation future | Note |
|---|---|---|
| BTC, ETH | API exchange dédiée (Binance/Coinbase, daily UTC ou 17:00 NY à trancher) | un panel binance M1 existe déjà dans Algo_claude (`data/panels/m5_indices/`), non branché |
| NKY, FTSE, CAC | source indices/futures séparée (le lake FTMO n'a que US500/US100/US30/GER40) | |
| BUND, TNOTE | source futures taux séparée (aucun taux dans le lake) | le COT T-NOTE 10A est déjà collecté — seule la jambe quotes manque |

Règle en attendant : ligne neutre `live=false`, composantes à 0, listés dans
`meta.excluded` — jamais de valeur simulée.

## TREND : composantes du score

- `mac` (0.20) : se branche sur le collecteur macro (FMP ou FairEconomy).
- `flow` (0.15) : flux — source à définir (ETF flows ? basis perp ? volumes).
- `meta.effective_weight` (0.65 actuellement) remonte automatiquement à
  mesure que les composantes s'allument ; pas de renormalisation, biais
  conservateur assumé (documenté README).

## Autres modules front encore en fixtures démo

- FX (force G8 inverse-vol + 28 paires) : dérivable du même lake quotes —
  candidat naturel après macro/GDELT.
- Saisonnalité, TDI, Micro : dérivables des quotes daily/M5.
- AIS (AISStream), MIL (OpenSky) : P2 fin de file, clés à provisionner.
- ZONES (convergence) : produit interne, fusion pondérée des calques — P3+.
