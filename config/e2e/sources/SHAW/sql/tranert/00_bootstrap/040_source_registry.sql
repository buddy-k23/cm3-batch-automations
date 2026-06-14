--------------------------------------------------------------------------------
-- TRANERT cross-source registry (dimension) table
--------------------------------------------------------------------------------
-- Maps the (LOCATION_CODE, ACTG_SYS_ID) pair to a logical SOURCE_SYSTEM and a
-- derived charge-off status. This is source-AGNOSTIC reference data shared by
-- every source system that feeds the TRANERT interface, hence the generalized
-- LKP_VALDO_TRANERT_ prefix (vs the SHAW-specific LKP_VALDO_SHAW_ lookups).
--
-- Key vocabulary
--   LOCATION_CODE  : recovery-system location (100020..100060).
--   ACTG_SYS_ID    : accounting-system id from uzapp_ad0.FINANCIAL_EXTRACT.
--                    This is the SAME code set as SYSTEM_ID_7000 in the
--                    uzapp_ad0.CDS_ACCT_EST view (FINANCIAL_EXTRACT is the
--                    authoritative, dedup-free source).
--   SOURCE_SYSTEM  : the logical source-system name (operator-assigned).
--   CHARGE_OFF_STATUS : derived from the ACTG_SYS_ID code family —
--                    'CHARGED_OFF'  for CAS,
--                    'WAREHOUSE'    for CASW,
--                    'MOVING_TO_CO' for *CO codes (last delinquent day,
--                                   charging off that day),
--                    NULL           otherwise.
--
-- *CO codes inherit SOURCE_SYSTEM from their base code (e.g. RNCO -> RN's
-- "Ready Now"). The invalid codes ECO/DCO/TVCO are intentionally NOT seeded.
--
-- Seed/refresh: 10_load/030_refresh_source_registry.sql (TRUNCATE + INSERT).
-- Idempotent CREATE via the ORA-00955-trapping block, matching
-- 010_lookup_tables.sql. Column widths mirror uzapp_ad0.FINANCIAL_EXTRACT
-- (LOCATION_CODE VARCHAR2(24), ACTG_SYS_ID VARCHAR2(16)).
--
-- Hard rule: no DML in this file (00_bootstrap/ is DDL-only).
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY (
      LOCATION_CODE     VARCHAR2(24) NOT NULL,
      ACTG_SYS_ID       VARCHAR2(16) NOT NULL,
      SOURCE_SYSTEM     VARCHAR2(80),
      CHARGE_OFF_STATUS VARCHAR2(20),
      CONSTRAINT PK_LKP_TRANERT_SOURCE_REGISTRY
        PRIMARY KEY (LOCATION_CODE, ACTG_SYS_ID),
      CONSTRAINT CK_LKP_TRANERT_SRC_REG_STATUS
        CHECK (CHARGE_OFF_STATUS IN ('CHARGED_OFF','WAREHOUSE','MOVING_TO_CO'))
    )
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/
