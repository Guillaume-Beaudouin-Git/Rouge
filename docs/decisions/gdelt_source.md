# Décision de source GDELT — calque NEWS (2026-06-10)

Contrat NEWSPTS : **lat, lon, titre, source, ts par point**.
Trois candidats testés sur réponses brutes avant d'écrire le collecteur.

## 1. GEO 2.0 API — ÉLIMINÉE (morte)

```
GET https://api.gdeltproject.org/api/v2/geo/geo?query=oil
HTTP 404
<title>404 Not Found</title>
<p>The requested URL was not found on this server.</p>
```

Endpoint retiré côté GDELT (testé avec et sans paramètres, http/https).

## 2. DOC 2.0 API — ÉLIMINÉE (rate-limit + pas de coordonnées)

```
GET https://api.gdeltproject.org/api/v2/doc/doc?query=oil&mode=artlist&format=json&maxrecords=3
HTTP 429
Please limit requests to one every 5 seconds or contact … for larger queries.
```

1 requête/5 s ; il faudrait une requête par catégorie, et le mode artlist
ne porte de toute façon **aucune coordonnée d'événement** (seulement
`sourcecountry`, pays du média).

## 3. Export GKG 2.1, fichiers 15 min — RETENUE

`http://data.gdeltproject.org/gdeltv2/lastupdate.txt` → ligne 3 :
`…/2026 06 10 03 30 00.gkg.csv.zip` (3,6 Mo zippé, 859 articles).
Mesuré sur le fichier `20260610033000.gkg.csv` complet :

| Critère du contrat | Champ GKG | Mesure |
|---|---|---|
| titre | XTRAS `[26]` `<PAGE_TITLE>` | **859/859 (100 %)** |
| lat/lon | V1Locations `[9]` (`type#nom#cc#adm1#lat#lon#fid`) | 666/859 (78 %) |
| source | SourceCommonName `[3]` | 100 % |
| ts | V2.1DATE `[1]` (`YYYYMMDDHHMMSS` UTC) | 100 % |
| catégorisation | V1Themes `[7]` (tokens `;`) | 277/859 matchent nos thèmes |

Extraits bruts (ligne réelle) :

```
[1]  20260610033000
[3]  moneycontrol.com
[7]  WB_698_TRADE;ECON_STOCKMARKET;AFFECT;WB_1150_VOLATILITY;…
[9]  1#South Korea#KS#KS#37#127.5#KS;1#Iran#IR#IR#32#53#IR
[26] …<PAGE_TITLE>Korea's Kospi extends losses on chipmakers, war-tied jitters</PAGE_TITLE>…
```

Fréquence des thèmes candidats sur ce seul fichier 15 min : ELECTION 108,
ARMEDCONFLICT 83, ECON_STOCKMARKET 44, ENV_OIL 40, MARITIME 35,
ECON_INFLATION 24, ECON_OILPRICE 21, FUELPRICES 10, BLOCKADE 9,
MARITIME_PIRACY 7, ECON_CENTRALBANK 6.

**Conséquences design** : pas de PAGE_TITLE → repli slug d'URL (prévu mais
non observé sur l'échantillon) ; pas de V1Locations → article ignoré
(22 %) ; un fichier par collecte, cadence et cache alignés 15 min ;
fixture de test = `tests/fixtures/gkg_sample.csv` (53 lignes réelles
extraites de ce fichier).
