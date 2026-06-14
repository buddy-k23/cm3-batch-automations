-- Thin wrapper: TRANERT driver scope (charged-off accounts paid off on
-- the batch date), augmented with C360 repo-fee columns. Every expected
-- view derives its driver-row population from this scope.
-- Helper body is materialized as a table in 00_bootstrap/030_expected_tables.sql
-- (shape B; the app_int account lacks CREATE VIEW, ORA-01031).
-- Mirrors: shaw.tranert.tranertSql
SELECT *
  FROM app_int.V_SHAW_TRANERT_DRIVER
