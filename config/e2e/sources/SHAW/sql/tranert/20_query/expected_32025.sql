-- L2b SQL-Truth expected rowset for TRANERT record type 32025 (COD).
--
-- Thin SELECT over the materialized app_int.EXPECTED_32025_TBL (CTAS table,
-- structure in 00_bootstrap/030_expected_tables.sql, populated by
-- 10_load/020_refresh_expected.sql). one_per_driver_row.
--
-- Format-alignment (issue #22)
-- ----------------------------
-- db_truth_comparator does strict trimmed-string equality, so every
-- reconciled column is projected in the SAME representation the fixed-width
-- file emits (per the COD mapping JSON):
--   * EFF-DAT-ERT, DAT-LAS-RPO-COD -> MM/DD/CCYY dates.
--   * DUE-DAT-DAY-COD is 9(2) -> zero-padded to width 2.
--   * RPO-COD-COD is 9(1); the table already stores a single 0/1 digit.
-- Only the reconciled columns are projected (explicit list, not t.*).
--
-- Key-column aliasing: see expected_32000.sql for the rationale.
SELECT TRIM(t.BK_NUM_ERT)                       AS BK_NUM_ERT,
       TRIM(t.TRN_COD_ERT)                      AS TRN_COD_ERT,
       TRIM(t.APP_ERT)                          AS APP_ERT,
       TRIM(t.LN_NUM_ERT)                       AS LN_NUM_ERT,
       TRIM(t.LN_NUM_ERT)                       AS "LN-NUM-ERT",
       TO_CHAR(t.EFF_DAT_ERT, 'MM/DD/YYYY')     AS EFF_DAT_ERT,
       TRIM(t.LGL_STA_COD_COD)                  AS LGL_STA_COD_COD,
       TRIM(TO_CHAR(t.RPO_COD_COD))             AS RPO_COD_COD,
       TO_CHAR(t.DAT_LAS_RPO_COD, 'MM/DD/YYYY') AS DAT_LAS_RPO_COD,
       LPAD(TO_CHAR(t.DUE_DAT_DAY_COD), 2, '0') AS DUE_DAT_DAY_COD,
       TRIM(t.ORG_LVL_NUM6_COD)                 AS ORG_LVL_NUM6_COD,
       TRIM(t.LCE_GEO_COD_COD)                  AS LCE_GEO_COD_COD,
       TRIM(t.OGL_LN_OFC_COD)                   AS OGL_LN_OFC_COD
  FROM app_int.EXPECTED_32025_TBL t
