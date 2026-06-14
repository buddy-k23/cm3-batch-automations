-- Thin wrapper: ACCT_NUM || '-' || na_cons_info_ind keyed values for
-- the mMiscCode2='W' short-circuit branch of CIF_CSM_INF_IND_CUS.
-- Helper body is materialized as a table in 00_bootstrap/030_expected_tables.sql
-- (shape B; the app_int account lacks CREATE VIEW, ORA-01031).
-- Mirrors: shaw.tranert.32005.loans.name.sql
SELECT *
  FROM app_int.V_SHAW_TRANERT_LOANS_NAME
