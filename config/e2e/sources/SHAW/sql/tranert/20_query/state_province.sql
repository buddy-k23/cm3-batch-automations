-- Thin wrapper: per-account state/province from coll_addr (primary-address
-- rows only, AA-marker excluded). Drives ST-COD-ORI fallback and populates
-- the per-batch stateProvinceMap.
-- Helper body is materialized as a table in 00_bootstrap/030_expected_tables.sql
-- (shape B; the app_int account lacks CREATE VIEW, ORA-01031).
-- Mirrors: shaw.tranert.tranert32010.sql
SELECT *
  FROM app_int.V_SHAW_TRANERT_STATE_PROVINCE
