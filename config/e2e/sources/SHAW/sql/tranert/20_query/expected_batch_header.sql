-- L2b SQL-Truth expected rowset for the TRANERT Batch Header record.
--
-- Thin SELECT over the materialized app_int.EXPECTED_BATCH_HEADER_TBL (CTAS
-- table, structure in 00_bootstrap/030_expected_tables.sql, populated LAST by
-- 10_load/020_refresh_expected.sql so ITM_CNT_BRT reflects the current detail
-- counts). Exactly one row.
--
-- Format-alignment (issue #22)
-- ----------------------------
-- db_truth_comparator does strict trimmed-string equality, so every
-- reconciled column is projected in the SAME representation the fixed-width
-- file emits (per the Batch Header mapping JSON). ITM-CNT-BRT is 9(9), so it
-- is zero-padded to width 9. Only the reconciled columns are projected
-- (explicit list, not t.*) so a formatted alias never collides with a raw
-- column of the same name.
--
-- Key-column aliasing
-- -------------------
-- The Batch Header is matched positionally (umbrella position: "first") and
-- the file key is BK-NUM-BRT. The key column is aliased to the dash form via
-- an Oracle quoted identifier so the spec `key` resolves on both sides.
SELECT TRIM(t.BK_NUM_BRT)                   AS BK_NUM_BRT,
       TRIM(t.BK_NUM_BRT)                   AS "BK-NUM-BRT",
       TRIM(t.APP_BRT)                      AS APP_BRT,
       TRIM(t.TRN_COD_BRT)                  AS TRN_COD_BRT,
       TRIM(t.BAT_TYP_BRT)                  AS BAT_TYP_BRT,
       LPAD(TO_CHAR(t.ITM_CNT_BRT), 9, '0') AS ITM_CNT_BRT
  FROM app_int.EXPECTED_BATCH_HEADER_TBL t
