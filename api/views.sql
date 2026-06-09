-- Vues DuckDB matérialisant chaque endpoint de l'API ROUGE.
-- Convention : une vue par endpoint, lisant les parquet partitionnés
-- par date sous data/<dataset>/date=YYYY-MM-DD/part.parquet.
-- Les vues réelles arrivent en P2, collecteur par collecteur ;
-- chaque vue ne lit que la DERNIÈRE partition disponible du dataset.

-- Exemple de patron (activé en P2 quand data/cot/ existera) :
-- CREATE OR REPLACE VIEW v_cot AS
-- SELECT * FROM read_parquet('data/cot/date=*/part.parquet', hive_partitioning=true)
-- WHERE date = (SELECT max(date) FROM read_parquet('data/cot/date=*/part.parquet', hive_partitioning=true));

-- v_cot      → GET /api/intel/cot
-- v_macro    → GET /api/intel/macro
-- v_trend    → GET /api/intel/trend
-- v_fx       → GET /api/intel/fx
-- v_markets  → GET /api/intel/markets
-- v_pm       → GET /api/intel/pm
-- v_layers   → GET /api/monitor/layers (news, pm, ais, mil, choke, zones)
