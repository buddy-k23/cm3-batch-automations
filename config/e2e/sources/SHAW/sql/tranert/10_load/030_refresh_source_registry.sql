-- ----------------------------------------------------------------
-- TRANERT cross-source registry: seed / refresh
-- ----------------------------------------------------------------
-- Populates app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY (created structure-only
-- by 00_bootstrap/040_source_registry.sql). TRUNCATE + INSERT so the harness
-- re-runs cleanly. No DROP.
--
-- Rows are the distinct trimmed (LOCATION_CODE, ACTG_SYS_ID) combinations
-- observed in uzapp_ad0.FINANCIAL_EXTRACT, with SOURCE_SYSTEM assigned by the
-- operator-supplied mapping and CHARGE_OFF_STATUS derived from the code:
--   CAS  -> CHARGED_OFF
--   CASW -> WAREHOUSE
--   *CO  -> MOVING_TO_CO (inherits SOURCE_SYSTEM from its base code)
--   else -> NULL
--
-- The invalid codes ECO / DCO / TVCO are intentionally excluded.
-- This is a static, hand-maintained dimension (NOT extractor-generated).
-- Edit SOURCE_SYSTEM here as the mapping evolves.
--
-- TRUNCATE is gated by the explicit _TRUNCATE_ALLOWLIST in
-- scripts/e2e_lib/shaw_tranert_smoke.py (enforced by a drift test).
--
-- Each statement is terminated by ';' on its own line so the bootstrap
-- statement splitter parses every statement independently.
-- ----------------------------------------------------------------

TRUNCATE TABLE app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
;

-- LOCATION_CODE 100020 --------------------------------------------------------
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100020', 'CAS', 'Service Finance Recovery Accounting', 'CHARGED_OFF')
;
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100020', 'SF', 'Service Finance', NULL)
;

-- LOCATION_CODE 100030 --------------------------------------------------------
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100030', 'BL', 'AFS', NULL)
;
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100030', 'CAS', 'shaw/lightstream/LRM/MTG/RN/AFS/SFC - Recovery Accounting', 'CHARGED_OFF')
;
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100030', 'CASW', 'Recovery Accounting Warehouse', 'WAREHOUSE')
;
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100030', 'EK', 'LightStream', NULL)
;
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100030', 'LS', 'Shaw', NULL)
;
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100030', 'RN', 'Ready Now', NULL)
;
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100030', 'RNCO', 'Ready Now', 'MOVING_TO_CO')
;
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100030', 'SFC', 'Sheffield', NULL)
;

-- LOCATION_CODE 100040 --------------------------------------------------------
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100040', 'CAS', 'Recovery Accounting', 'CHARGED_OFF')
;
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100040', 'DP', 'DDA', NULL)
;

-- LOCATION_CODE 100050 --------------------------------------------------------
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100050', 'CAS', 'Tsys Consumer Recovery Accounting', 'CHARGED_OFF')
;
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100050', 'CASW', 'Tsys Consumer Recovery Accounting Warehouse', 'WAREHOUSE')
;
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100050', 'TSM', 'Tsys Consumer', NULL)
;
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100050', 'TSV', 'Tsys Consumer', NULL)
;

-- LOCATION_CODE 100060 --------------------------------------------------------
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100060', 'CAS', 'Tsys Commercial charged off Recovery', 'CHARGED_OFF')
;
INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY
  (LOCATION_CODE, ACTG_SYS_ID, SOURCE_SYSTEM, CHARGE_OFF_STATUS)
  VALUES ('100060', 'TSM', 'Tsys Commercial', NULL)
;
