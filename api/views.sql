-- Vues DuckDB matérialisant chaque endpoint de l'API ROUGE.
-- Convention : les chemins 'data/…' sont relatifs à la racine du repo ;
-- api/db.py les réécrit en absolu au chargement. Chaque vue _latest ne
-- sert que le dernier point disponible du dataset (repli stale géré par
-- l'API via la date embarquée).

-- ============================================================ COT
-- Brut : une ligne par contrat × semaine (partition = date de rapport).
CREATE OR REPLACE VIEW v_cot_raw AS
SELECT sym, iso, ord, category, report_date, net, long, short, open_interest
FROM read_parquet('data/cot/date=*/part.parquet');

-- GET /api/intel/cot — forme front : name, iso, pctl, z, dwk, crowd.
-- pctl  : percentile du net courant dans la fenêtre 3 ans (156 sem.)
-- z     : z-score du net vs même fenêtre
-- dwk   : variation hebdo du net, en milliers de contrats
-- crowd : saturation P>=90 / P<=10
CREATE OR REPLACE VIEW v_cot AS
WITH cur AS (
  SELECT r.* FROM v_cot_raw r
  JOIN (SELECT sym, max(report_date) AS d FROM v_cot_raw GROUP BY sym) m
    ON r.sym = m.sym AND r.report_date = m.d
),
hist AS (
  SELECT c.sym, c.iso, c.ord, c.report_date, c.net, h.net AS hnet
  FROM cur c
  JOIN v_cot_raw h
    ON h.sym = c.sym
   AND h.report_date >  c.report_date - INTERVAL 1092 DAY  -- 156 semaines exactement
   AND h.report_date <= c.report_date
),
prev AS (
  SELECT c.sym, r.net AS prev_net
  FROM cur c
  JOIN v_cot_raw r ON r.sym = c.sym AND r.report_date < c.report_date
  QUALIFY row_number() OVER (PARTITION BY c.sym ORDER BY r.report_date DESC) = 1
),
agg AS (
  SELECT h.sym, any_value(h.iso) AS iso, any_value(h.ord) AS ord,
         any_value(h.report_date) AS report_date,
         any_value(h.net) AS net,
         CAST(round(100.0 * avg(CASE WHEN h.hnet <= h.net THEN 1 ELSE 0 END)) AS INT) AS pctl,
         round((any_value(h.net) - avg(h.hnet)) / nullif(stddev_samp(h.hnet), 0), 2) AS z,
         count(*) AS n_weeks
  FROM hist h GROUP BY h.sym
)
SELECT a.sym AS name, a.iso, a.report_date, a.net, a.pctl,
       coalesce(a.z, 0.0) AS z,
       round((a.net - coalesce(p.prev_net, a.net)) / 1000.0, 1) AS dwk,
       (a.pctl >= 90 OR a.pctl <= 10) AS crowd,
       a.n_weeks, a.ord
FROM agg a LEFT JOIN prev p ON p.sym = a.sym
ORDER BY a.ord;

-- ============================================================ PM
-- Brut : un point de probabilité par marché par collecte (part-HHMMSS).
CREATE OR REPLACE VIEW v_pm_raw AS
SELECT category, event_title, event_slug, market_id, market_slug,
       q, p, p_raw, d, vol_num, vol24h, end_date, display, lon, lat, ord,
       snapshot_ts
FROM read_parquet('data/pm/date=*/part-*.parquet');

-- GET /api/intel/pm — forme front : q, p, d, vol, lon, lat.
-- Dernier snapshot, marchés display uniquement, ordre du mapping.
CREATE OR REPLACE VIEW v_pm AS
SELECT q, p, d, vol_num, lon, lat, ord, snapshot_ts
FROM v_pm_raw
WHERE display AND snapshot_ts = (SELECT max(snapshot_ts) FROM v_pm_raw)
ORDER BY ord;

-- ============================================================ TREND
-- Table TREND assemblée par collectors/trend_builder.py : une partition
-- par session de référence (jointure quotes daily + COT, mac/flow neutres
-- flaggés). asof_session = session globale du build, identique sur les 26
-- lignes — c'est elle qui sélectionne le dernier build complet.
CREATE OR REPLACE VIEW v_trend_raw AS
SELECT * FROM read_parquet('data/trend/date=*/part.parquet');

-- GET /api/intel/trend — forme front (26 lignes triées g décroissant).
CREATE OR REPLACE VIEW v_trend AS
SELECT cat, sym, name, f1, f2, g, mom, mac, pos, risk, flow, d30, chg,
       live, pos_available, asof_session
FROM v_trend_raw
WHERE asof_session = (SELECT max(asof_session) FROM v_trend_raw)
ORDER BY g DESC;

-- ============================================================ NEWS
-- Brut : un point par article matché par collecte GKG 15 min.
CREATE OR REPLACE VIEW v_news_raw AS
SELECT * FROM read_parquet('data/news_raw/date=*/part-*.parquet');

-- GET /api/monitor/layers (bloc news) — sélection anti-bruit du dernier
-- snapshot : dédup + clusters <50 km + cap, calculée par news_collector.
CREATE OR REPLACE VIEW v_news AS
SELECT lon, lat, w, title, source, ts, category, n, snapshot_ts
FROM read_parquet('data/news_sel/date=*/part-*.parquet')
WHERE snapshot_ts = (SELECT max(snapshot_ts)
                     FROM read_parquet('data/news_sel/date=*/part-*.parquet'))
ORDER BY w DESC, ts DESC;

-- ============================================================ FX
-- Force G8 + 28 paires assemblées par collectors/fx_builder.py.
CREATE OR REPLACE VIEW v_fx_strength AS
SELECT c, iso, now, s, asof_session
FROM read_parquet('data/fx_strength/date=*/part.parquet')
WHERE asof_session = (SELECT max(asof_session)
                      FROM read_parquet('data/fx_strength/date=*/part.parquet'))
ORDER BY now DESC;

CREATE OR REPLACE VIEW v_fx_pairs AS
SELECT p, b, q, diff, trend, conflict, asof_session
FROM read_parquet('data/fx_pairs/date=*/part.parquet')
WHERE asof_session = (SELECT max(asof_session)
                      FROM read_parquet('data/fx_pairs/date=*/part.parquet'))
ORDER BY abs(diff) DESC;

-- ============================================================ SEASON
CREATE OR REPLACE VIEW v_season AS
SELECT sym, month, mean_pct, hit_pct, n_years, live, asof_session
FROM read_parquet('data/season/date=*/part.parquet')
WHERE asof_session = (SELECT max(asof_session)
                      FROM read_parquet('data/season/date=*/part.parquet'))
ORDER BY sym, month;

-- ============================================================ TDI
CREATE OR REPLACE VIEW v_tdi AS
SELECT flux, met, z, note, live, asof_session
FROM read_parquet('data/tdi/date=*/part.parquet')
WHERE asof_session = (SELECT max(asof_session)
                      FROM read_parquet('data/tdi/date=*/part.parquet'))
ORDER BY abs(z) DESC;

-- ============================================================ MICRO
CREATE OR REPLACE VIEW v_micro_hours AS
SELECT a, hour, pctl, live, asof_session
FROM read_parquet('data/micro_hours/date=*/part.parquet')
WHERE asof_session = (SELECT max(asof_session)
                      FROM read_parquet('data/micro_hours/date=*/part.parquet'))
ORDER BY a, hour;

CREATE OR REPLACE VIEW v_micro_leadlag AS
SELECT pair, lag, corr, n, live, asof_session
FROM read_parquet('data/micro_leadlag/date=*/part.parquet')
WHERE asof_session = (SELECT max(asof_session)
                      FROM read_parquet('data/micro_leadlag/date=*/part.parquet'))
ORDER BY abs(corr) DESC NULLS LAST;

-- ============================================================ à venir (P2)
-- v_macro    → GET /api/intel/macro
-- v_trend    → GET /api/intel/trend
-- v_fx       → GET /api/intel/fx
-- v_markets  → GET /api/intel/markets
-- v_layers   → GET /api/monitor/layers (news, pm, ais, mil, choke, zones)
