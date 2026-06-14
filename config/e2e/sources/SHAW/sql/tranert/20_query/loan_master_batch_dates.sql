-- Thin wrapper: distinct truncated batch dates from SHAW_LOAN_MASTER,
-- bounded by BATCH_DATE_LOCATOR's max, sorted DESC.
-- Helper body is materialized as a table in 00_bootstrap/030_expected_tables.sql
-- (shape B; the app_int account lacks CREATE VIEW, ORA-01031).
-- Mirrors: shaw.loanmaster.sql.batch.dates
SELECT *
  FROM app_int.V_SHAW_TRANERT_LOAN_MASTER_BATCH_DATES
