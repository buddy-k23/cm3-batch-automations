-- L2b SQL-Truth expected rowset for TRANERT record type 32075 (REC).
--
-- Thin SELECT over the materialized app_int.EXPECTED_32075_TBL (CTAS table,
-- structure in 00_bootstrap/030_expected_tables.sql, populated by
-- 10_load/020_refresh_expected.sql). one_per_driver_row; RCF_DUE_REC is
-- sourced from V_SHAW_TRANERT_COST_MERGED.
--
-- Format-alignment (issue #22)
-- ----------------------------
-- db_truth_comparator does strict trimmed-string equality, so every
-- reconciled column is projected in the SAME representation the fixed-width
-- file emits (per the REC mapping JSON):
--   * EFF-DAT-ERT -> MM/DD/CCYY date.
--   * RCF-DUE-REC (-Z(12).9(2)) -> 2 forced decimals, leading-zero
--     suppressed, so a zero cost-merge total renders '.00' (matching the
--     file) rather than '0' (see expected_32010.sql for the mask rationale).
-- Only the reconciled columns are projected (explicit list, not t.*).
--
-- Key-column aliasing: see expected_32000.sql for the rationale.
SELECT TRIM(t.BK_NUM_ERT)                       AS BK_NUM_ERT,
       TRIM(t.TRN_COD_ERT)                      AS TRN_COD_ERT,
       TRIM(t.APP_ERT)                          AS APP_ERT,
       TRIM(t.LN_NUM_ERT)                       AS LN_NUM_ERT,
       TRIM(t.LN_NUM_ERT)                       AS "LN-NUM-ERT",
       TO_CHAR(t.EFF_DAT_ERT, 'MM/DD/YYYY')     AS EFF_DAT_ERT,
       LTRIM(TO_CHAR(t.RCF_DUE_REC, 'FM999999999990.00'), '0')
                                                AS RCF_DUE_REC,
       TRIM(t.RCF_REF_NUM_REC)                  AS RCF_REF_NUM_REC,
       TRIM(t.EXP_PYF_IND_REC)                  AS EXP_PYF_IND_REC
  FROM app_int.EXPECTED_32075_TBL t
