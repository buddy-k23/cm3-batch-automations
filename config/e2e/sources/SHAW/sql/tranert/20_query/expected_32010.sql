-- L2b SQL-Truth expected rowset for TRANERT record type 32010 (ORI).
--
-- Thin SELECT over the materialized app_int.EXPECTED_32010_TBL (CTAS table,
-- structure in 00_bootstrap/030_expected_tables.sql, populated by
-- 10_load/020_refresh_expected.sql). zero_or_one_per_driver_row: the refresh
-- already filters CHG_OFF_CD = '1', so this rowset only contains the
-- predicate-true accounts (28 for batch 2026-05-28).
--
-- Format-alignment (issue #22)
-- ----------------------------
-- db_truth_comparator does strict trimmed-string equality, so every
-- reconciled column is projected in the SAME representation the fixed-width
-- file emits (per the ORI mapping JSON):
--   * dates  -> MM/DD/CCYY  (TO_CHAR ..., 'MM/DD/YYYY')
--   * amounts (-Z(12).9(2)) -> 2 forced decimals, no thousands separator,
--     leading integer-zero suppressed so 0 renders '.00' and 0.5 -> '.50'
--     (FM mask gives a leading 0, stripped by LTRIM(...,'0')).
-- Only the reconciled columns are projected (explicit list, not t.*).
--
-- Key-column aliasing: see expected_32000.sql for the rationale.
SELECT TRIM(t.BK_NUM_ERT)                          AS BK_NUM_ERT,
       TRIM(t.TRN_COD_ERT)                         AS TRN_COD_ERT,
       TRIM(t.APP_ERT)                             AS APP_ERT,
       TRIM(t.LN_NUM_ERT)                          AS LN_NUM_ERT,
       TRIM(t.LN_NUM_ERT)                          AS "LN-NUM-ERT",
       TO_CHAR(t.EFF_DAT_ERT, 'MM/DD/YYYY')        AS EFF_DAT_ERT,
       TO_CHAR(t.OGL_CONTRACT_DAT_ORI, 'MM/DD/YYYY') AS OGL_CONTRACT_DAT_ORI,
       LTRIM(TO_CHAR(t.OGL_NTE_AMT_ORI, 'FM999999999990.00'), '0')
                                                   AS OGL_NTE_AMT_ORI,
       LTRIM(TO_CHAR(t.OGL_COF_INT_AMT_ORI, 'FM999999999990.00'), '0')
                                                   AS OGL_COF_INT_AMT_ORI,
       LTRIM(TO_CHAR(t.OGL_CONTRACT_AMT_ORI, 'FM999999999990.00'), '0')
                                                   AS OGL_CONTRACT_AMT_ORI,
       TO_CHAR(t.OGL_NTE_DAT_ORI, 'MM/DD/YYYY')    AS OGL_NTE_DAT_ORI,
       TRIM(t.COF_REA_COD_ORI)                     AS COF_REA_COD_ORI,
       LTRIM(TO_CHAR(t.OGL_PMT_AMT_ORI, 'FM999999999990.00'), '0')
                                                   AS OGL_PMT_AMT_ORI,
       TO_CHAR(t.DAT_INT_ACR_TO_ORI, 'MM/DD/YYYY') AS DAT_INT_ACR_TO_ORI,
       TO_CHAR(t.PCOF_DAT_LAS_PMT_ORI, 'MM/DD/YYYY') AS PCOF_DAT_LAS_PMT_ORI,
       TRIM(t.ST_COD_ORI)                          AS ST_COD_ORI
  FROM app_int.EXPECTED_32010_TBL t
