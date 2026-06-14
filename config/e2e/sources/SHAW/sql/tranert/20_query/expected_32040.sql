-- L2b SQL-Truth expected rowset for TRANERT record type 32040 (CBRS).
--
-- Thin SELECT over the materialized app_int.EXPECTED_32040_TBL (CTAS table,
-- structure in 00_bootstrap/030_expected_tables.sql, populated by
-- 10_load/020_refresh_expected.sql). one_per_driver_row.
--
-- Format-alignment (issue #22)
-- ----------------------------
-- db_truth_comparator does strict trimmed-string equality, so every
-- reconciled column is projected in the SAME representation the fixed-width
-- file emits (per the CBRS mapping JSON):
--   * EFF-DAT-ERT, DAT-DLQ-STR-CBRS -> MM/DD/CCYY dates.
--   * HGH-AMT-DLQ-CBRS (-Z(12).9(2)) -> 2 forced decimals, leading-zero
--     suppressed (see expected_32010.sql for the mask rationale).
-- Only the reconciled columns are projected (explicit list, not t.*).
--
-- Key-column aliasing: see expected_32000.sql for the rationale.
SELECT TRIM(t.BK_NUM_ERT)                          AS BK_NUM_ERT,
       TRIM(t.TRN_COD_ERT)                         AS TRN_COD_ERT,
       TRIM(t.APP_ERT)                             AS APP_ERT,
       TRIM(t.LN_NUM_ERT)                          AS LN_NUM_ERT,
       TRIM(t.LN_NUM_ERT)                          AS "LN-NUM-ERT",
       TO_CHAR(t.EFF_DAT_ERT, 'MM/DD/YYYY')        AS EFF_DAT_ERT,
       TO_CHAR(t.DAT_DLQ_STR_CBRS, 'MM/DD/YYYY')   AS DAT_DLQ_STR_CBRS,
       LTRIM(TO_CHAR(t.HGH_AMT_DLQ_CBRS, 'FM999999999990.00'), '0')
                                                   AS HGH_AMT_DLQ_CBRS,
       TRIM(t.PRE_COF_L1_NUM_CBRS)                 AS PRE_COF_L1_NUM_CBRS,
       TRIM(t.ACT_TYP_CBRS)                        AS ACT_TYP_CBRS
  FROM app_int.EXPECTED_32040_TBL t
