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

-- ============================================================ à venir (P2)
-- v_pm       → GET /api/intel/pm
-- v_macro    → GET /api/intel/macro
-- v_trend    → GET /api/intel/trend
-- v_fx       → GET /api/intel/fx
-- v_markets  → GET /api/intel/markets
-- v_layers   → GET /api/monitor/layers (news, pm, ais, mil, choke, zones)
