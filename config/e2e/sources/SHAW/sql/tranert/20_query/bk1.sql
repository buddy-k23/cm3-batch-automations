-- Thin wrapper: per-account most-recent BK1 row (5-column TRANERT variant
-- with chapter + dismissed/discharge/bankruptcy dates). NOT the older
-- 2-column shaw.bk1map; the TRANERT mapper consumes this 5-column form.
-- Helper body is materialized as a table in 00_bootstrap/030_expected_tables.sql
-- (shape B; the app_int account lacks CREATE VIEW, ORA-01031).
-- Mirrors: shaw.tranert.bk1map
SELECT *
  FROM app_int.V_SHAW_TRANERT_BK1
