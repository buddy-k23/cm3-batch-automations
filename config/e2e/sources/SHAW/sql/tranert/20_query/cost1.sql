-- Thin wrapper: cost1 contribution to RCF-DUE-REC (current charge-off
-- fees from APP_INT.shaw_charge_off ∩ APP_INT.shaw_loan_master).
-- Helper body is materialized as a table in 00_bootstrap/030_expected_tables.sql
-- (shape B; the app_int account lacks CREATE VIEW, ORA-01031).
-- Mirrors: shaw.tranert.37025.fee.cost1.sql
SELECT *
  FROM app_int.V_SHAW_TRANERT_COST1
