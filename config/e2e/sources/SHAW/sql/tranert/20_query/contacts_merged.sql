-- Thin wrapper: APPS ∪ APP_INT contact rows for driver-scoped accounts,
-- deduplicated by (ACCT_NUM, CONTACT_ID) with APPS winning ties.
-- Helper body is materialized as a table in 00_bootstrap/030_expected_tables.sql
-- (shape B; the app_int account lacks CREATE VIEW, ORA-01031).
-- Mirrors: shaw.tranert.tranert32005.sql.appsdb ∪ shaw.tranert.tranert32005.sql.app_intdb
SELECT *
  FROM app_int.V_SHAW_TRANERT_CONTACTS_MERGED
