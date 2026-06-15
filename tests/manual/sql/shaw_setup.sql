-- =============================================================================
-- shaw_setup.sql — SHAW manual-test schema bootstrap for Valdo Engine v3.
--
-- Purpose
-- -------
-- Stand up the Oracle objects the optional L2b reconciliation step (TEST_PLAN.md
-- §3 / Scenario 6) needs:
--   1. Six SHAW_* staging tables that mirror config/e2e/sources/SHAW.yml::
--      staging_tables. These are placeholders today: input-file mappings
--      (SHAW_COLLATERAL_MASTER.json etc.) are 1-field TODO stubs from EC-S9,
--      so each table carries a minimal but useful shape suitable for the
--      file_to_staging gate smoke check. Replace with real columns when the
--      BA workbook adds the missing input-file layouts and you re-run
--      `valdo onboard-source`.
--   2. Seven EXPECTED_*_TBL tables derived from the TRANERT umbrella's seven
--      per-record-type mapping JSONs (the curated EC-S4/S5 dedup; NEW1
--      covers both 32000 / 32001 from the umbrella). Column names use
--      mapping `target_name` per EC-S4 conventions. Types follow the spec
--      rules: string|char -> VARCHAR2(length), int|num|numeric|decimal ->
--      NUMBER(length) (precision capped at Oracle's 38-digit limit), date
--      -> DATE.
--   3. Sample INSERTs at the bottom populating the EXPECTED_*_TBL rows that
--      correspond to the deterministic content of
--      tests/manual/fixtures/tranert_shaw_test_valid.txt produced by
--      scripts/build_shaw_test_files.py.
--
-- Schema / tablespace
-- -------------------
-- Run as the user that should own the tables. Suggested patterns:
--
--   Oracle XE in Docker (default APP_INT user from the dev compose stack):
--     sqlplus app_int/<pwd>@localhost:1521/FREEPDB1 @tests/manual/sql/shaw_setup.sql
--
--   On a shared environment where APP_INT already exists (matches
--   config/e2e/sources/SHAW.yml::staging_schema):
--     sqlplus app_int/<pwd>@<host>:<port>/<service> @tests/manual/sql/shaw_setup.sql
--
-- If you need to create the schema first, uncomment one of these blocks:
--   -- CREATE USER app_int IDENTIFIED BY "<change-me>" QUOTA UNLIMITED ON USERS;
--   -- GRANT CONNECT, RESOURCE, CREATE SESSION, CREATE TABLE TO app_int;
--   -- ALTER SESSION SET CURRENT_SCHEMA = APP_INT;
--
-- The DROP block at the bottom of this file is commented out — uncomment
-- to tear down between iterations.
--
-- BA workbook note (EC-S9):
--   Input-file mappings (SHAW_COLLATERAL_MASTER.json, SHAW_FEE_MASTER.json,
--   SHAW_LOAN_MASTER.json, SHAW_LOANS_NAME.json, SHAW_POSTED_TRANS.json,
--   SHAW_TRANS_MASTER.json) are 1-field TODO stubs. The SHAW_* tables below
--   are intentionally minimal (BATCH_DATE / RECORD_KEY / LINE_NO / RAW_LINE
--   / LOAD_TS) so the file_to_staging gate has something to land into.
--   Fill in real columns when the BA workbook is updated and re-run
--   `valdo onboard-source`.
-- =============================================================================

SET ECHO ON
SET FEEDBACK ON
WHENEVER SQLERROR CONTINUE

-- -----------------------------------------------------------------------------
-- 1) SHAW_* staging tables (placeholder shape — see EC-S9 note above).
-- -----------------------------------------------------------------------------

CREATE TABLE SHAW_COLLATERAL (
    BATCH_DATE  DATE,
    RECORD_KEY  VARCHAR2(50),
    LINE_NO     NUMBER,
    RAW_LINE    VARCHAR2(4000),
    LOAD_TS     TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE TABLE SHAW_FEE_MASTER (
    BATCH_DATE  DATE,
    RECORD_KEY  VARCHAR2(50),
    LINE_NO     NUMBER,
    RAW_LINE    VARCHAR2(4000),
    LOAD_TS     TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE TABLE SHAW_LOAN_MASTER (
    BATCH_DATE  DATE,
    RECORD_KEY  VARCHAR2(50),
    LINE_NO     NUMBER,
    RAW_LINE    VARCHAR2(4000),
    LOAD_TS     TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE TABLE SHAW_LOANS_NAME (
    BATCH_DATE  DATE,
    RECORD_KEY  VARCHAR2(50),
    LINE_NO     NUMBER,
    RAW_LINE    VARCHAR2(4000),
    LOAD_TS     TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE TABLE SHAW_TRANSACTIONS (
    BATCH_DATE  DATE,
    RECORD_KEY  VARCHAR2(50),
    LINE_NO     NUMBER,
    RAW_LINE    VARCHAR2(4000),
    LOAD_TS     TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE TABLE SHAW_TRANS_MASTER (
    BATCH_DATE  DATE,
    RECORD_KEY  VARCHAR2(50),
    LINE_NO     NUMBER,
    RAW_LINE    VARCHAR2(4000),
    LOAD_TS     TIMESTAMP DEFAULT SYSTIMESTAMP
);

-- -----------------------------------------------------------------------------
-- 2) EXPECTED_*_TBL reconciliation tables.
--
-- One table per TRANERT record type; columns derived from the matching
-- mapping JSON's `target_name` and `data_type` / `length`. Identifiers
-- that begin with a digit (e.g. 10_98_RPT_IND_COD, 24_CYC_DLQ_*_CBRS)
-- are wrapped in double quotes — required by Oracle for non-leading-letter
-- identifiers.
-- -----------------------------------------------------------------------------

-- BATCH_HEADER — umbrella position: "first"; 22 fields.
CREATE TABLE EXPECTED_BATCH_HEADER_TBL (
    BK_NUM_BRT          NUMBER(5),
    APP_BRT             NUMBER(3),
    EFF_DAT_BRT         DATE,
    TRN_COD_BRT         VARCHAR2(5),
    BAT_NUM_BRT         NUMBER(7),
    INP_SRC_COD_BRT     NUMBER(3),
    BAT_TYP_BRT         NUMBER(2),
    OPR_ID_BRT          VARCHAR2(8),
    ORG_LVL_NUM_1_BRT   NUMBER(7),
    ORG_LVL_NUM_2_BRT   NUMBER(7),
    ORG_LVL_NUM_3_BRT   NUMBER(7),
    ORG_LVL_NUM_4_BRT   NUMBER(7),
    ORG_LVL_NUM_5_BRT   NUMBER(7),
    ORG_LVL_NUM_6_BRT   NUMBER(7),
    ORG_LVL_NUM_7_BRT   NUMBER(7),
    ORG_LVL_NUM_8_BRT   NUMBER(7),
    ORG_LVL_NUM_9_BRT   NUMBER(7),
    ORG_LVL_NUM_10_BRT  NUMBER(7),
    ORG_LVL_NUM_11_BRT  NUMBER(7),
    ORG_LVL_NUM_12_BRT  NUMBER(7),
    ITM_CNT_BRT         NUMBER(9),
    DR_CR_AMT_BRT       NUMBER(22)
);

-- NEW1 — TRN-COD-ERT 32000 (NAS) / 32001 (EAS); 14 fields.
CREATE TABLE EXPECTED_NEW1_TBL (
    BK_NUM_ERT          NUMBER(5),
    APP_ERT             NUMBER(3),
    LN_NUM_ERT          VARCHAR2(18),
    REF_NUM_ERT         VARCHAR2(3),
    FILLER              VARCHAR2(130),
    EFF_DAT_ERT         DATE,
    TRN_COD_ERT         NUMBER(5),
    BAT_ITM_NUM_ERT     NUMBER(9),
    INP_SRC_COD_ERT     NUMBER(3),
    TRN_CNT_ERT         NUMBER(3),
    TRN_MOR_DTA_FLG_ERT VARCHAR2(1),
    SEL_ID_NEW          VARCHAR2(1),
    LCT_COD_NEW1        VARCHAR2(6),
    USR_ID_NEW1         VARCHAR2(8)
);

-- CUS — TRN-COD-ERT 32005; 35 fields.
CREATE TABLE EXPECTED_CUS_TBL (
    BK_NUM_ERT                    NUMBER(5),
    APP_ERT                       NUMBER(3),
    LN_NUM_ERT                    VARCHAR2(18),
    REF_NUM_ERT                   VARCHAR2(3),
    FILLER                        VARCHAR2(130),
    EFF_DAT_ERT                   DATE,
    TRN_COD_ERT                   NUMBER(5),
    BAT_ITM_NUM_ERT               NUMBER(9),
    INP_SRC_COD_ERT               NUMBER(3),
    TRN_CNT_ERT                   NUMBER(3),
    TRN_MOR_DTA_FLG_ERT           VARCHAR2(1),
    CIF_ACT_NUM_CUS               VARCHAR2(24),
    CIF_ACT_COD_CUS               VARCHAR2(1),
    CIF_CBR_RPT_IND_CUS           VARCHAR2(1),
    CIF_CSM_INF_IND_CUS           VARCHAR2(2),
    DAT_BKY_REC_CUS               DATE,
    CIF_ACT_COD_DES_CUS           VARCHAR2(18),
    CIF_REF_NUM_CUS               VARCHAR2(3),
    LAS_CSM_INF_IND_CUS           VARCHAR2(2),
    ECOA_CODE_CUS                 VARCHAR2(1),
    LAST_ECOA_CODE_CUS            VARCHAR2(1),
    LAST_BUREAU_RECORDED_DATE_CUS DATE,
    FINAL_REPORT_INDICATOR_CUS    VARCHAR2(1),
    LAST_REPORTED_SEG_TYPE_CUS    VARCHAR2(2),
    LAST_CHEX_RECORDED_DATE_CUS   DATE,
    ACTION_CODE                   VARCHAR2(1),
    CONTACT_ID                    VARCHAR2(24),
    LOCATION_CODE                 VARCHAR2(6),
    ACCT_NUM                      VARCHAR2(18),
    NAME_RELATIONSHIP             VARCHAR2(1),
    LEAD_CONTACT_IND              NUMBER(1),
    RESPONSIBLE_PARTY             NUMBER(1),
    CAS_ADDRESS_IND               VARCHAR2(1),
    EXTERNAL_SYSTEM_ID            VARCHAR2(4),
    PREFERRED_CURRENCY            VARCHAR2(3)
);

-- ORI — TRN-COD-ERT 32010; 48 fields.
CREATE TABLE EXPECTED_ORI_TBL (
    BK_NUM_ERT             NUMBER(5),
    APP_ERT                NUMBER(3),
    LN_NUM_ERT             VARCHAR2(18),
    REF_NUM_ERT            VARCHAR2(3),
    FILLER                 VARCHAR2(130),
    EFF_DAT_ERT            DATE,
    TRN_COD_ERT            NUMBER(5),
    BAT_ITM_NUM_ERT        NUMBER(9),
    INP_SRC_COD_ERT        NUMBER(3),
    TRN_CNT_ERT            NUMBER(3),
    TRN_MOR_DTA_FLG_ERT    VARCHAR2(1),
    OGL_CONTRACT_DAT_ORI   DATE,
    OGL_TRM_ORI            NUMBER(3),
    OGL_MAT_DAT_ORI        DATE,
    OGL_CONTRACT_AMT_ORI   NUMBER(22),
    OGL_PORTFOLIO_TYP_ORI  VARCHAR2(1),
    LN_TYP_ORI             NUMBER(3),
    COF_CDN_IND_ORI        NUMBER(1),
    OGL_NTE_DAT_ORI        DATE,
    OGL_NTE_AMT_ORI        NUMBER(22),
    OGL_COF_INT_AMT_ORI    NUMBER(22),
    INT_RT_ORI             NUMBER(7),
    OGL_INT_RT_ORI         NUMBER(7),
    REP_TYP_ORI            NUMBER(3),
    HGH_BAL_ORI            NUMBER(22),
    AMT_PAS_DUE_30_ORI     NUMBER(22),
    AMT_PAS_DUE_60_ORI     NUMBER(22),
    AMT_PAS_DUE_90_ORI     NUMBER(22),
    AMT_PAS_DUE_120_ORI    NUMBER(22),
    OGL_NUM_PMT_MTD_ORI    NUMBER(5),
    OGL_NUM_PMT_YTD_ORI    NUMBER(5),
    OGL_NUM_PMT_LTD_ORI    NUMBER(5),
    OGL_PMT_AMT_MTD_ORI    NUMBER(22),
    OGL_PMT_AMT_YTD_ORI    NUMBER(22),
    OGL_PMT_AMT_LTD_ORI    NUMBER(22),
    FIL_FLD_ORI            VARCHAR2(3),
    COF_REA_COD_ORI        VARCHAR2(3),
    ST_COD_ORI             VARCHAR2(3),
    DAT_INT_ACR_TO_ORI     DATE,
    ACT_STA_ORI            NUMBER(1),
    MTG_AGC_ID_ORI         VARCHAR2(2),
    PCOF_DAT_LAS_PMT_ORI   DATE,
    MTG_ID_NUM_ORI         VARCHAR2(18),
    DAT_LAS_STM_ORI        DATE,
    PCOF_PMT_AMT_LAS_ORI   NUMBER(22),
    CNV_ACT_NUM_ORI        VARCHAR2(20),
    OGL_PMT_AMT_ORI        NUMBER(22),
    DAT_ACT_CLS_TO_ATY_ORI DATE
);

-- COD — TRN-COD-ERT 32025; 57 fields.
-- "10_98_RPT_IND_COD" is quoted because Oracle identifiers can't start with a digit.
CREATE TABLE EXPECTED_COD_TBL (
    BK_NUM_ERT               NUMBER(5),
    APP_ERT                  NUMBER(3),
    LN_NUM_ERT               VARCHAR2(18),
    REF_NUM_ERT              VARCHAR2(3),
    FILLER                   VARCHAR2(130),
    EFF_DAT_ERT              DATE,
    TRN_COD_ERT              NUMBER(5),
    BAT_ITM_NUM_ERT          NUMBER(9),
    INP_SRC_COD_ERT          NUMBER(3),
    TRN_CNT_ERT              NUMBER(3),
    TRN_MOR_DTA_FLG_ERT      VARCHAR2(1),
    STP_ACR_ATY_COD_COD      NUMBER(1),
    DAT_PLC_IN_STP_COD       DATE,
    DAT_STP_ACR_RMD_COD      DATE,
    INT_ACR_WHI_STP_COD      NUMBER(22),
    LGL_STA_COD_COD          VARCHAR2(3),
    DAT_LAS_RPO_COD          DATE,
    RPO_COD_COD              NUMBER(1),
    DUE_DAT_DAY_COD          NUMBER(2),
    DAT_RMD_RPO_COD          DATE,
    FCL_STA_IND_COD          VARCHAR2(1),
    ORG_LVL_NUM_1_COD        NUMBER(7),
    ORG_LVL_NUM_2_COD        NUMBER(7),
    ORG_LVL_NUM_3_COD        NUMBER(7),
    ORG_LVL_NUM_4_COD        NUMBER(7),
    ORG_LVL_NUM_5_COD        NUMBER(7),
    ORG_LVL_NUM_6_COD        NUMBER(7),
    ORG_LVL_NUM_7_COD        NUMBER(7),
    ORG_LVL_NUM_8_COD        NUMBER(7),
    ORG_LVL_NUM_9_COD        NUMBER(7),
    ORG_LVL_NUM_10_COD       NUMBER(7),
    ORG_LVL_NUM_11_COD       NUMBER(7),
    ORG_LVL_NUM_12_COD       NUMBER(7),
    "10_98_RPT_IND_COD"      VARCHAR2(1),
    LCE_GEO_COD_COD          VARCHAR2(9),
    CR_RT_COD                VARCHAR2(4),
    CAL_RPT_COD_COD          VARCHAR2(4),
    OGL_LN_POO_NUM_COD       NUMBER(7),
    OGL_LN_OFC_COD           VARCHAR2(9),
    LN_OFC_CUR_COD           VARCHAR2(9),
    OGL_BR_NUM_COD           NUMBER(6),
    LN_PUR_COD_COD           VARCHAR2(3),
    LN_CAT_COD               NUMBER(3),
    DLR_NUM_COD              NUMBER(7),
    FCL_EFF_DAT_COD          DATE,
    STM_FRQ_COD              VARCHAR2(1),
    NATL_CURRENCY_COD        VARCHAR2(3),
    PREF_CURRENCY_COD        VARCHAR2(3),
    BASE_CURRENCY_COD        VARCHAR2(3),
    PRT_COF_BK_NUM_COD       NUMBER(5),
    PRT_COF_APP_COD          NUMBER(3),
    PRT_COF_LN_NUM_COD       VARCHAR2(18),
    NAS_SRC_COD_COD          NUMBER(3),
    HMDA_ULI_CODE            VARCHAR2(45),
    IRS_PROP_SECURE_TYPE_COD VARCHAR2(8),
    IRS_PROP_ADDR_DESC_COD   VARCHAR2(39),
    IRS_NUM_OF_MORT_COD      NUMBER(4)
);

-- CBRS — TRN-COD-ERT 32040; 69 fields.
-- The 22 "24_CYC_DLQ_*" identifiers must be quoted (leading digit).
CREATE TABLE EXPECTED_CBRS_TBL (
    BK_NUM_ERT             NUMBER(5),
    APP_ERT                NUMBER(3),
    LN_NUM_ERT             VARCHAR2(18),
    REF_NUM_ERT            VARCHAR2(3),
    FILLER                 VARCHAR2(130),
    EFF_DAT_ERT            DATE,
    TRN_COD_ERT            NUMBER(5),
    BAT_ITM_NUM_ERT        NUMBER(9),
    INP_SRC_COD_ERT        NUMBER(3),
    TRN_CNT_ERT            NUMBER(3),
    TRN_MOR_DTA_FLG_ERT    VARCHAR2(1),
    DAT_DLQ_STR_CBRS       DATE,
    M2F_CMT_COD_CBRS       VARCHAR2(2),
    HGH_DAY_DLQ_CBRS       NUMBER(5),
    M2F_CMP_CON_COD_CBRS   VARCHAR2(2),
    HGH_AMT_DLQ_CBRS       NUMBER(22),
    PRE_COF_L1_NUM_CBRS    VARCHAR2(30),
    "24_CYC_DLQ_01_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_12_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_23_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_02_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_13_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_24_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_03_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_14_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_25_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_04_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_15_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_05_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_16_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_06_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_17_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_07_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_18_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_08_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_19_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_09_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_20_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_10_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_21_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_11_CBRS"   VARCHAR2(1),
    "24_CYC_DLQ_22_CBRS"   VARCHAR2(1),
    ACT_TYP_CBRS           VARCHAR2(3),
    LAS_LN_BAL_CBRS        NUMBER(22),
    LAS_DAT_RPT_CBRS       DATE,
    LAS_ACT_STA_CBRS       VARCHAR2(2),
    LAS_CMT_COD_CBRS       VARCHAR2(2),
    LAS_CMP_COD_CBRS       VARCHAR2(2),
    CUR_PMT_RTG_CBRS       VARCHAR2(1),
    PET_DAT_CBRS           DATE,
    PET_ACT_STA_CBRS       VARCHAR2(2),
    PET_SCH_PMT_AMT_CBRS   NUMBER(22),
    PET_CUR_BAL_CBRS       NUMBER(22),
    PET_AMT_PAS_DUE_CBRS   NUMBER(22),
    PET_PMT_RTG_CBRS       VARCHAR2(1),
    CHX_RTG_TRN_NUM_CBRS   VARCHAR2(9),
    CHX_ACT_NUM_CBRS       VARCHAR2(20),
    CHX_ACT_TYP_CBRS       VARCHAR2(3),
    CHX_REA_COD_1_CBRS     VARCHAR2(2),
    CHX_REA_COD_2_CBRS     VARCHAR2(2),
    CHX_REA_COD_3_CBRS     VARCHAR2(2),
    CHX_DIS_IND_CBRS       VARCHAR2(1),
    CHX_LAS_DAT_RPT_CBRS   DATE,
    CHX_LAS_REA_RPT_CBRS   VARCHAR2(2),
    CHX_LAS_CLO_STA_CBRS   VARCHAR2(2),
    SEC_LAS_DAT_RPT_CBRS   DATE,
    SEC_LN_NUM_CBRS        VARCHAR2(30),
    SEC_LAS_ACT_STA_CBRS   VARCHAR2(2),
    SEC_LAS_LN_BAL_CBRS    NUMBER(22)
);

-- REC — TRN-COD-ERT 32075; 35 fields.
CREATE TABLE EXPECTED_REC_TBL (
    BK_NUM_ERT          NUMBER(5),
    APP_ERT             NUMBER(3),
    LN_NUM_ERT          VARCHAR2(18),
    REF_NUM_ERT         VARCHAR2(3),
    FILLER              VARCHAR2(130),
    EFF_DAT_ERT         DATE,
    TRN_COD_ERT         NUMBER(5),
    BAT_ITM_NUM_ERT     NUMBER(9),
    INP_SRC_COD_ERT     NUMBER(3),
    TRN_CNT_ERT         NUMBER(3),
    TRN_MOR_DTA_FLG_ERT VARCHAR2(1),
    RCF_REF_NUM_REC     VARCHAR2(3),
    RCF_FEE_DES_REC     VARCHAR2(30),
    RCF_AMT_REC         NUMBER(22),
    RCF_PCT_REC         NUMBER(7),
    RCF_ASE_COD_REC     NUMBER(1),
    RCF_INT_IND_REC     NUMBER(1),
    RCF_MAX_FEE_AMT_REC NUMBER(22),
    RCF_DES_COD_REC     NUMBER(3),
    RCF_MAX_FEE_LTD_REC NUMBER(22),
    RCF_FEE_WVE_IND_REC VARCHAR2(1),
    RCF_OGL_AMT_REC     NUMBER(22),
    EXP_PYF_IND_REC     VARCHAR2(1),
    RCF_ICR_COD_REC     NUMBER(1),
    CST_DFC_IND_REC     VARCHAR2(1),
    CMB_PMT_PTY_REC     NUMBER(3),
    RCF_DUE_REC         NUMBER(22),
    FCL_CST_PD_YTD_REC  NUMBER(22),
    RCF_CAP_LTD_REC     NUMBER(22),
    RCF_ASE_MTD_REC     NUMBER(22),
    RCF_PD_MTD_REC      NUMBER(22),
    RCF_ASE_YTD_REC     NUMBER(22),
    RCF_PD_YTD_REC      NUMBER(22),
    RCF_ASE_LTD_REC     NUMBER(22),
    RCF_PD_LTD_REC      NUMBER(22)
);

-- -----------------------------------------------------------------------------
-- 3) Sample INSERTs — match tests/manual/fixtures/tranert_shaw_test_valid.txt.
--
-- The valid fixture has 1 BATCH_HEADER + 5 NEW1 + 4 CUS + 3 ORI + 2 COD +
-- 2 CBRS + 2 REC (= 18 detail rows, ITM-CNT-BRT=000000018). LN_NUM_ERT is
-- generated as 'LN' + 16-digit sequence ('LN0000000000000001'..). The
-- BATCH_HEADER's EFF_DAT_BRT and detail EFF_DAT_ERT are both 2026-06-01;
-- TRN_COD_BRT = 'BATCH', BK_NUM_ERT = 1, APP_ERT = 200 across the board.
-- Only one INSERT per table is emitted (enough for the L2b smoke test to
-- compare apples-to-apples). Add more rows when you extend the fixture.
-- -----------------------------------------------------------------------------

INSERT INTO EXPECTED_BATCH_HEADER_TBL (
    BK_NUM_BRT, APP_BRT, EFF_DAT_BRT, TRN_COD_BRT, BAT_NUM_BRT,
    INP_SRC_COD_BRT, BAT_TYP_BRT, OPR_ID_BRT,
    ORG_LVL_NUM_1_BRT, ORG_LVL_NUM_2_BRT, ORG_LVL_NUM_3_BRT,
    ORG_LVL_NUM_4_BRT, ORG_LVL_NUM_5_BRT, ORG_LVL_NUM_6_BRT,
    ORG_LVL_NUM_7_BRT, ORG_LVL_NUM_8_BRT, ORG_LVL_NUM_9_BRT,
    ORG_LVL_NUM_10_BRT, ORG_LVL_NUM_11_BRT, ORG_LVL_NUM_12_BRT,
    ITM_CNT_BRT, DR_CR_AMT_BRT
) VALUES (
    1, 200, DATE '2026-06-01', 'BATCH', 1,
    1, 32, 'VALDOTST',
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    18, 0
);

INSERT INTO EXPECTED_NEW1_TBL (
    BK_NUM_ERT, APP_ERT, LN_NUM_ERT, EFF_DAT_ERT, TRN_COD_ERT
) VALUES (
    1, 200, 'LN0000000000000001', DATE '2026-06-01', 32000
);

INSERT INTO EXPECTED_CUS_TBL (
    BK_NUM_ERT, APP_ERT, LN_NUM_ERT, EFF_DAT_ERT, TRN_COD_ERT
) VALUES (
    1, 200, 'LN0000000000000001', DATE '2026-06-01', 32005
);

INSERT INTO EXPECTED_ORI_TBL (
    BK_NUM_ERT, APP_ERT, LN_NUM_ERT, EFF_DAT_ERT, TRN_COD_ERT
) VALUES (
    1, 200, 'LN0000000000000001', DATE '2026-06-01', 32010
);

INSERT INTO EXPECTED_COD_TBL (
    BK_NUM_ERT, APP_ERT, LN_NUM_ERT, EFF_DAT_ERT, TRN_COD_ERT
) VALUES (
    1, 200, 'LN0000000000000001', DATE '2026-06-01', 32025
);

INSERT INTO EXPECTED_CBRS_TBL (
    BK_NUM_ERT, APP_ERT, LN_NUM_ERT, EFF_DAT_ERT, TRN_COD_ERT
) VALUES (
    1, 200, 'LN0000000000000001', DATE '2026-06-01', 32040
);

INSERT INTO EXPECTED_REC_TBL (
    BK_NUM_ERT, APP_ERT, LN_NUM_ERT, EFF_DAT_ERT, TRN_COD_ERT
) VALUES (
    1, 200, 'LN0000000000000001', DATE '2026-06-01', 32075
);

COMMIT;

-- -----------------------------------------------------------------------------
-- Optional teardown — uncomment as a block when you want to reset between runs.
-- -----------------------------------------------------------------------------
-- DROP TABLE EXPECTED_REC_TBL PURGE;
-- DROP TABLE EXPECTED_CBRS_TBL PURGE;
-- DROP TABLE EXPECTED_COD_TBL PURGE;
-- DROP TABLE EXPECTED_ORI_TBL PURGE;
-- DROP TABLE EXPECTED_CUS_TBL PURGE;
-- DROP TABLE EXPECTED_NEW1_TBL PURGE;
-- DROP TABLE EXPECTED_BATCH_HEADER_TBL PURGE;
-- DROP TABLE SHAW_TRANS_MASTER PURGE;
-- DROP TABLE SHAW_TRANSACTIONS PURGE;
-- DROP TABLE SHAW_LOANS_NAME PURGE;
-- DROP TABLE SHAW_LOAN_MASTER PURGE;
-- DROP TABLE SHAW_FEE_MASTER PURGE;
-- DROP TABLE SHAW_COLLATERAL PURGE;

EXIT
