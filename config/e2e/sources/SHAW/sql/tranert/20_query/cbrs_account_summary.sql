-- Thin wrapper: per-account CBRS BASE-segment summary for driver-scoped
-- accounts. Used by both EXPECTED_32040_VIEW (CBRS field projections) and
-- EXPECTED_32005_VIEW (CIF-CBR-RPT-IND-CUS branching).
-- Helper body is materialized as a table in 00_bootstrap/030_expected_tables.sql
-- (shape B; the app_int account lacks CREATE VIEW, ORA-01031).
-- Mirrors: shaw.cbrs.account.summary.sql
SELECT *
  FROM app_int.V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY
