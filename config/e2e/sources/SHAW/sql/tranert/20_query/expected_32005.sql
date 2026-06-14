-- L2b SQL-Truth expected rowset for TRANERT record type 32005 (CUS).
--
-- Thin SELECT over the materialized app_int.EXPECTED_32005_TBL (CTAS table,
-- structure in 00_bootstrap/030_expected_tables.sql, populated by
-- 10_load/020_refresh_expected.sql). many_per_driver_row: one row per
-- non-skipped contact, composite key (ACCT_NUM, CONTACT_ID).
--
-- Format-alignment (issue #22)
-- ----------------------------
-- db_truth_comparator does strict trimmed-string equality, so every
-- reconciled column is projected in the SAME representation the fixed-width
-- file emits (per the CUS mapping JSON). EFF-DAT-ERT is a MM/DD/CCYY date.
-- Only the reconciled columns are projected (explicit list, not t.*).
--
-- Key-column aliasing
-- -------------------
-- The reconciliation spec `key` is matched against BOTH the parsed file row
-- (dash field names, e.g. LN-NUM-ERT / CONTACT-ID) and this rowset. The key
-- columns are aliased to the dash form via Oracle quoted identifiers so the
-- same key string resolves on both sides.
SELECT TRIM(t.BK_NUM_ERT)                   AS BK_NUM_ERT,
       TRIM(t.TRN_COD_ERT)                  AS TRN_COD_ERT,
       TRIM(t.APP_ERT)                      AS APP_ERT,
       TRIM(t.LN_NUM_ERT)                   AS LN_NUM_ERT,
       TRIM(t.LN_NUM_ERT)                   AS "LN-NUM-ERT",
       TO_CHAR(t.EFF_DAT_ERT, 'MM/DD/YYYY') AS EFF_DAT_ERT,
       TRIM(t.CONTACT_ID)                   AS CONTACT_ID,
       TRIM(t.CONTACT_ID)                   AS "CONTACT-ID",
       TRIM(t.ECOA_CODE_CUS)                AS ECOA_CODE_CUS,
       TRIM(t.CIF_CSM_INF_IND_CUS)          AS CIF_CSM_INF_IND_CUS
  FROM app_int.EXPECTED_32005_TBL t
