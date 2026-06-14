-- Thin wrapper: per-account most-recent BK3 row (5-column TRANERT variant).
-- See bk1.sql for the bk1 vs shaw.bk1map distinction.
-- Helper body is materialized as a table in 00_bootstrap/030_expected_tables.sql
-- (shape B; the app_int account lacks CREATE VIEW, ORA-01031).
-- Mirrors: shaw.tranert.bk3map
SELECT *
  FROM app_int.V_SHAW_TRANERT_BK3
