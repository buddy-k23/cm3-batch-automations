-- L2b SQL-Truth expected rowset for TRANERT record type 32000 (NEW1).
--
-- Thin SELECT over the materialized app_int.EXPECTED_32000_TBL (CTAS table,
-- structure in 00_bootstrap/030_expected_tables.sql, populated by
-- 10_load/020_refresh_expected.sql). The db_truth_comparator engine
-- (scripts/e2e_lib/db_truth_comparator.py) executes this file and indexes
-- the rows by the reconciliation spec's `key`.
--
-- Format-alignment (issue #22)
-- ----------------------------
-- db_truth_comparator does strict trimmed-string equality, so every
-- reconciled column is projected in the SAME representation the fixed-width
-- file emits (per the NEW1 mapping JSON). EFF-DAT-ERT is a MM/DD/CCYY date.
-- Only the reconciled columns are projected (explicit list, not t.*).
--
-- Key-column aliasing
-- -------------------
-- The reconciliation spec `key` is matched against BOTH the parsed file row
-- (mapping-JSON dash form, e.g. LN-NUM-ERT) and this rowset. The key column
-- is aliased to the dash form via an Oracle quoted identifier so the same
-- key string resolves on both sides.
SELECT TRIM(t.BK_NUM_ERT)                       AS BK_NUM_ERT,
       TRIM(t.TRN_COD_ERT)                      AS TRN_COD_ERT,
       TRIM(t.APP_ERT)                          AS APP_ERT,
       TRIM(t.LN_NUM_ERT)                       AS LN_NUM_ERT,
       TRIM(t.LN_NUM_ERT)                       AS "LN-NUM-ERT",
       TO_CHAR(t.EFF_DAT_ERT, 'MM/DD/YYYY')     AS EFF_DAT_ERT,
       TRIM(t.LCT_COD_NEW1)                     AS LCT_COD_NEW1
  FROM app_int.EXPECTED_32000_TBL t
