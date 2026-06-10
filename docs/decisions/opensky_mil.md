# OpenSky — auth, budget crédits, filtrage MIL (2026-06-10)

## Auth OAuth2 (vérifiée empiriquement)

Nouveau portail OpenSky = identifiants client_credentials Keycloak :

```
POST https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token
     grant_type=client_credentials&client_id=…&client_secret=…
→ HTTP 200 {access_token, expires_in: 1800, token_type: Bearer}
```

TTL 1800 s → cache du token avec refresh anticipé à 80 % (24 min).

## Budget crédits (mesuré)

`GET /api/states/all` global (sans bbox), authentifié :

```
HTTP/2 200
x-rate-limit-remaining: 3996   ← après UN appel sur quota 4000/jour
```

→ l'appel global coûte le maximum, **4 crédits**. À la cadence retenue de
**10 min** : 144 appels/jour × 4 = **576 crédits = 14,4 % du quota** —
très en deçà du plafond auto-imposé de 50 % (2000). Marge pour densifier
à 5 min (28,8 %) si besoin ; pas de bbox nécessaire à ce budget.

## Filtrage militaire — heuristique assumée

Aucun flag « militaire » dans l'API : le filtre combine préfixes de
callsign et plages hex ICAO (api/config/mil_filter.yaml). Sources :
allocations ICAO 24-bit publiques (bloc US DoD AE0000–AFFFFF), listes
communautaires tar1090/ADSBexchange pour les préfixes. Vérifié sur un
states/all réel : 17 hits par préfixe (GAF, IAM, NATO, CTM, BAF, DUKE,
RRR…) + 20 par plage hex US (CARD, HAZE, MOJO…).

Couverture **partielle par nature** (README) : transpondeurs militaires
souvent coupés en opération, callsigns banalisés, plages hex de nombreux
pays non documentées. Le calque montre ce qui est visible, pas ce qui vole.
