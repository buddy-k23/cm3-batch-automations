-- Thin wrapper: per-account RCF-DUE-REC = cost1 ∪ cost2 reduction,
-- with cost2 entries filling ONLY accounts not in cost1.
-- Helper body is materialized as a table in 00_bootstrap/030_expected_tables.sql
-- (shape B; the app_int account lacks CREATE VIEW, ORA-01031).
-- Mirrors: Java DAOOperations.getTranert() cost1 ∪ cost2 logic.
SELECT *
  FROM app_int.V_SHAW_TRANERT_COST_MERGED
