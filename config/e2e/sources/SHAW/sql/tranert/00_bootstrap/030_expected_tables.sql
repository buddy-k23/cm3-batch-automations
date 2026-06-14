--------------------------------------------------------------------------------
-- SHAW TRANERT L2b SQL-Truth gate: materialized helper tables (CTAS)
--------------------------------------------------------------------------------
-- Shape (B), session 7. The app_int account that owns the harness objects
-- holds CREATE TABLE / SELECT ANY TABLE but NOT CREATE VIEW (confirmed via
-- session_privs: every CREATE VIEW fails ORA-01031). The L2b helper result
-- sets — formerly authored as V_SHAW_TRANERT_* helper VIEWS
-- — are therefore materialized as app_int-owned TABLES via
-- CREATE TABLE ... AS SELECT (CTAS), and refreshed each run by
-- 10_load/020_refresh_expected.sql (TRUNCATE + INSERT ... SELECT).
--
-- This file is STRUCTURE-ONLY + idempotent:
--   * Each table is created via an idempotent PL/SQL block that traps
--     ORA-00955 (name already in use), exactly like 010_lookup_tables.sql,
--     so re-running the bootstrap is a no-op for already-created tables.
--   * The CTAS body is wrapped as ``SELECT * FROM ( <helper body> )
--     WHERE 1 = 0`` so the table is created with the correct column shape
--     and types but ZERO rows. The row population happens in the refresh
--     file. This keeps "define structure" and "load data" cleanly separated
--     and avoids any DROP (the app_int account, and the harness whitelist,
--     forbid DROP).
--   * Oracle alternative quoting ``q'[ ... ]'`` wraps the EXECUTE IMMEDIATE
--     payload so the single quotes inside the helper bodies (e.g. '1',
--     'APPS', the FEE_CODE_KEY IN-list) need no doubling.
--
-- Build order matters: V_SHAW_TRANERT_COST_MERGED reads
-- V_SHAW_TRANERT_COST1, so COST1 is created (structurally) before
-- COST_MERGED. All other helpers are independent.
--
-- Sources of truth for every body:
--   - _ref/shaw-sql-statements.properties
--   - config/templates/TranertMapper (1).java
--   - config/templates/DAOOperations (2).java
--
-- Hard rule: no DML in this file (00_bootstrap/ is DDL-only). The
-- INSERT ... SELECT refresh lives in 10_load/020_refresh_expected.sql.
--------------------------------------------------------------------------------


--------------------------------------------------------------------------------
-- V_SHAW_TRANERT_LOAN_MASTER_BATCH_DATES  (structure only)
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.V_SHAW_TRANERT_LOAN_MASTER_BATCH_DATES AS
      SELECT * FROM (
        SELECT DISTINCT TRUNC(BATCH_DATE) AS BATCH_DATE
          FROM app_int.SHAW_LOAN_MASTER
         WHERE TRUNC(BATCH_DATE) <= (
                 SELECT TRUNC(MAX(BATCH_DATE))
                   FROM app_int.BATCH_DATE_LOCATOR
               )
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- V_SHAW_TRANERT_DRIVER  (structure only)
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.V_SHAW_TRANERT_DRIVER AS
      SELECT * FROM (
        SELECT c1.*,
               c2.REPOSESSION_FEE_7186,
               c2.NSF_FEES_7159,
               c2.LATE_CHARGES_7157,
               c2.MISC_REBATES_CREDITS_7145,
               c2.OTHER_REPO_FEES_7146,
               c2.AUCTION_FEES_7147,
               c2.EXPENSE_COLLECTED_7148,
               c2.INTEREST_COLLECTED_7149,
               CAST('' AS VARCHAR2(80)) AS CTM_CODE_DESC
          FROM (SELECT a1.*
                  FROM app_int.SHAW_LOAN_MASTER a1
                 WHERE a1.CHG_OFF_CD = '1'
                   AND TRUNC(a1.M_DATE_PAID_OFF) = TRUNC(a1.BATCH_DATE)
               ) c1
          LEFT JOIN APP_INT.C360_REPO_FEE_CDS c2
                 ON c1.ACCT_NUM = SUBSTR(c2.ACCT_NUM, 0, 10)
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- V_SHAW_TRANERT_COST1  (structure only; built before COST_MERGED)
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.V_SHAW_TRANERT_COST1 AS
      SELECT * FROM (
        SELECT c2.ACCT_NUM,
               c1.UNPAID_LCHRGS,
               c1.FEE_CURR_BAL,
               CAST('' AS VARCHAR2(10)) AS FEE_CODE_KEY
          FROM APP_INT.shaw_charge_off c1,
               APP_INT.shaw_loan_master c2
         WHERE c1.ACCOUNT_NUM = c2.ACCT_NUM
           AND c1.UNPAID_LCHRGS IS NOT NULL
           AND c1.FEE_CURR_BAL  IS NOT NULL
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- V_SHAW_TRANERT_COST_MERGED  (structure only; reads V_SHAW_TRANERT_COST1)
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.V_SHAW_TRANERT_COST_MERGED AS
      SELECT * FROM (
        WITH driver_batch_dates AS (
          SELECT BATCH_DATE,
                 ROW_NUMBER() OVER (ORDER BY BATCH_DATE DESC) AS rn
            FROM (SELECT DISTINCT TRUNC(BATCH_DATE) AS BATCH_DATE
                    FROM app_int.SHAW_LOAN_MASTER
                   WHERE TRUNC(BATCH_DATE) <= (
                           SELECT TRUNC(MAX(BATCH_DATE))
                             FROM app_int.BATCH_DATE_LOCATOR
                         ))
        ),
        history_rank_window AS (
          SELECT BATCH_DATE,
                 DENSE_RANK() OVER (ORDER BY BATCH_DATE DESC) AS r
            FROM (SELECT DISTINCT BATCH_DATE
                    FROM app_int.SHAW_LOAN_MASTER_HISTORY)
        ),
        cost2_primary AS (
          SELECT a1.ACCT_NUM,
                 NVL(a1.UNPAID_LCHRGS, 0) AS UNPAID_LCHRGS,
                 NVL(a3.FEE_CURR_BAL, 0)  AS FEE_CURR_BAL
            FROM app_int.SHAW_LOAN_MASTER_HISTORY a1
            LEFT JOIN APP_INT.SHAW_FEE_MASTER_HISTORY a3
                   ON a1.ACCT_NUM   = a3.ACCT_NUM
                  AND a1.BATCH_DATE = a3.BATCH_DATE
                  AND a3.FEE_CODE_KEY IN (
                        'BKAP','BKRP','CNRF','DAUC','DANL',
                        'DFLD','DHAZ','DKEY','DMSC','DNDP',
                        'DNSF','DOTH','DPPY','DREP','DSTO',
                        'DTAX','FAPP','FLEG','RLEG','RPRD',
                        'RPRS','SAUC','SFMS','SFNS','SFOC',
                        'SKEY','SPPY','SREP','SSTO'
                      )
           CROSS JOIN app_int.SHAW_LOAN_MASTER a2
           WHERE a1.ACCT_NUM = a2.ACCT_NUM
             AND a2.CHG_OFF_CD = '1'
             AND TRUNC(a2.M_DATE_PAID_OFF) = TRUNC(a2.BATCH_DATE)
             AND TRUNC(a2.BATCH_DATE) = (SELECT BATCH_DATE
                                           FROM driver_batch_dates
                                          WHERE rn = 1)
             AND TRUNC(a1.BATCH_DATE) = (SELECT BATCH_DATE
                                           FROM history_rank_window
                                          WHERE r = 2)
        ),
        -- Cost aggregation mirrors Java DAOOperations.get32075Cost(): UNPAID_LCHRGS
        -- is a loan-master-level value (constant across an account's per-fee rows),
        -- so it is counted ONCE per account (MAX) while FEE_CURR_BAL is summed
        -- across rows. SUM(UNPAID + FEE) double-counts UNPAID for accounts with
        -- multiple fee rows (issue #22).
        --
        -- The Java size==2 batch-dates branch (get32075SQLCost2 putAll primary
        -- THEN putAll secondary, last-write-wins per account) is UNREACHABLE
        -- here: app_int.SHAW_LOAN_MASTER never carries more than one distinct
        -- TRUNC(BATCH_DATE), so driver_batch_dates.rn=2 never exists and the
        -- secondary cost2 slice is always empty. The dead cost2_secondary CTE
        -- and the cost2_union UNION ALL were removed (issue #22 / §5.1);
        -- cost2_union now reads cost2_primary directly.
        cost1_agg AS (
          SELECT ACCT_NUM,
                 MAX(NVL(UNPAID_LCHRGS, 0)) + SUM(NVL(FEE_CURR_BAL, 0))
           AS RCF_DUE_REC
            FROM app_int.V_SHAW_TRANERT_COST1
           GROUP BY ACCT_NUM
        ),
        cost2_union AS (
          SELECT ACCT_NUM,
                 MAX(UNPAID_LCHRGS) + SUM(FEE_CURR_BAL) AS RCF_DUE_REC
            FROM cost2_primary
           GROUP BY ACCT_NUM
        )
        SELECT ACCT_NUM, RCF_DUE_REC FROM cost1_agg
        UNION ALL
        SELECT cu.ACCT_NUM, cu.RCF_DUE_REC
          FROM cost2_union cu
         WHERE cu.ACCT_NUM NOT IN (SELECT ACCT_NUM FROM cost1_agg)
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY  (structure only)
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY AS
      SELECT * FROM (
        SELECT ACCOUNT_NUMBER,
               PORTFOLIO_TYPE,
               ACCOUNT_TYPE,
               ACCOUNT_STATUS,
               PAST_DUE,
               TERMS_FREQ,
               DATE_CLOSED
          FROM app_int.cbrs_TRW_SUMMARY
         WHERE SEGMENT = 'BASE'
           AND account_number IN (
                 SELECT acct_num
                   FROM app_int.SHAW_LOAN_MASTER
                  WHERE CHG_OFF_CD = '1'
                    AND TRUNC(M_DATE_PAID_OFF) = TRUNC(BATCH_DATE)
               )
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- V_SHAW_TRANERT_LOANS_NAME  (structure only)
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.V_SHAW_TRANERT_LOANS_NAME AS
      SELECT * FROM (
        SELECT a1.ACCT_NUM || '-' || a2.na_cons_info_ind AS KEYED_VALUE
          FROM app_int.SHAW_LOAN_MASTER a1,
               app_int.SHAW_LOANS_NAME  a2
         WHERE a1.ACCT_NUM   = a2.ACCT_NUM
           AND a1.CHG_OFF_CD = '1'
           AND a2.na_cons_info_ind IS NOT NULL
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- V_SHAW_TRANERT_CONTACTS_MERGED  (structure only; cross-schema uzapp_ad0.*)
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.V_SHAW_TRANERT_CONTACTS_MERGED AS
      SELECT * FROM (
        WITH apps_leg AS (
          -- Every projected column is CAST to an explicit VARCHAR2 width so
          -- the UNION ALL with app_int_leg is datatype-aligned (ORA-01790).
          -- The two legs read the same logical fields from DIFFERENT physical
          -- tables (APPS uzapp_ad0.* vs app_int.*) whose column types/lengths
          -- differ; the Java runs them as two separate queries and merges in
          -- code, so normalizing to a common string type here is faithful.
          SELECT 'APPS' AS SOURCE_DB,
                 CAST(a1.CONTACT_ID        AS VARCHAR2(40)) AS CONTACT_ID,
                 CAST(a1.NAME_RELATIONSHIP AS VARCHAR2(10)) AS NAME_RELATIONSHIP,
                 CAST(a1.GOVT_ID_NUM       AS VARCHAR2(30)) AS GOVT_ID_NUM,
                 CAST(a1.LEADCONTACT       AS VARCHAR2(10)) AS LEAD_CONTACT_IND,
                 CAST(a1.ACCT_NUM          AS VARCHAR2(40)) AS ACCT_NUM,
                 CAST(a2.CIF_ACT_COD       AS VARCHAR2(20)) AS CIF_ACT_COD_CUS_RAW,
                 CAST(a3.CONS_INFO_IND     AS VARCHAR2(20)) AS CONS_INFO_IND,
                 CAST(a3.SEGMENT           AS VARCHAR2(20)) AS CBRS_SEGMENT,
                 CAST(a3.TERMS_FREQ        AS VARCHAR2(20)) AS CBRS_TERMS_FREQ,
                 CAST(a3.ECOA_CODE         AS VARCHAR2(20)) AS ECOA_CODE_CUS
            FROM (SELECT c2.CONTACT_ID,
                         c4.name_relationship,
                         c2.govt_id_num,
                         c4.lead_contact_ind AS leadContact,
                         c3.ACCT_NUM
                    FROM uzapp_ad0.coll_contact c2,
                         app_int.SHAW_LOAN_MASTER c3,
                         uzapp_ad0.CONTACT_ACCOUNT c4
                   WHERE c4.ACCT_NUM   = c3.ACCT_NUM
                     AND c3.CHG_OFF_CD = '1'
                     AND c4.contact_id = c2.CONTACT_ID) a1
            LEFT JOIN uzapp_ad0.LOAN_CUST_INFO a2
                   ON a1.ACCT_NUM   = a2.ln_num
                  AND a1.CONTACT_ID = a2.CIF_ACT_NUM
            LEFT JOIN app_int.CBRS_TRW_SUMMARY a3
                   ON a1.ACCT_NUM     = a3.ACCOUNT_NUMBER
                  AND a1.govt_id_num  = LPAD(a3.SSN, 9, 0)
        ),
        app_int_leg AS (
          -- Same explicit CASTs as apps_leg so the UNION ALL aligns.
          SELECT 'APP_INT' AS SOURCE_DB,
                 CAST(a1.CONTACT_ID        AS VARCHAR2(40)) AS CONTACT_ID,
                 CAST(a1.NAME_RELATIONSHIP AS VARCHAR2(10)) AS NAME_RELATIONSHIP,
                 CAST(a1.GOVT_ID_NUM       AS VARCHAR2(30)) AS GOVT_ID_NUM,
                 CAST(a1.LEADCONTACT       AS VARCHAR2(10)) AS LEAD_CONTACT_IND,
                 CAST(a1.ACCT_NUM          AS VARCHAR2(40)) AS ACCT_NUM,
                 CAST(a2.CIF_ACT_COD       AS VARCHAR2(20)) AS CIF_ACT_COD_CUS_RAW,
                 CAST(a3.CONS_INFO_IND     AS VARCHAR2(20)) AS CONS_INFO_IND,
                 CAST(a3.SEGMENT           AS VARCHAR2(20)) AS CBRS_SEGMENT,
                 CAST(a3.TERMS_FREQ        AS VARCHAR2(20)) AS CBRS_TERMS_FREQ,
                 CAST(a3.ECOA_CODE         AS VARCHAR2(20)) AS ECOA_CODE_CUS
            FROM (SELECT c4.contact_id,
                         c4.lead_contact_ind AS leadContact,
                         c4.name_relationship,
                         c2.govt_tax_id      AS govt_id_num,
                         c3.ACCT_NUM
                    FROM app_int.CONTACT          c2,
                         app_int.SHAW_LOAN_MASTER c3,
                         app_int.CONTACT_ACCOUNT  c4
                   WHERE c4.ACCOUNT_NUM = c3.ACCT_NUM
                     AND c4.SRC_SYSTEM  = 'SHAW'
                     AND c2.SRC_SYSTEM  = 'SHAW'
                     AND c2.RECORD_TP   = '1'
                     AND c3.CHG_OFF_CD  = '1'
                     AND c4.contact_id  = c2.CONTACT_ID) a1
            LEFT JOIN uzapp_ad0.LOAN_CUST_INFO a2
                   ON a1.ACCT_NUM   = a2.ln_num
                  AND a1.CONTACT_ID = a2.CIF_ACT_NUM
            LEFT JOIN app_int.CBRS_TRW_SUMMARY a3
                   ON a1.ACCT_NUM     = a3.ACCOUNT_NUMBER
                  AND a1.govt_id_num  = LPAD(a3.SSN, 9, 0)
        ),
        unioned AS (
          SELECT * FROM apps_leg
          UNION ALL
          SELECT * FROM app_int_leg
        ),
        ranked AS (
          SELECT u.*,
                 ROW_NUMBER() OVER (
                   PARTITION BY ACCT_NUM, CONTACT_ID
                   ORDER BY CASE SOURCE_DB WHEN 'APPS' THEN 0 ELSE 1 END
                 ) AS dedup_rn
            FROM unioned u
        )
        SELECT CONTACT_ID,
               NAME_RELATIONSHIP,
               GOVT_ID_NUM,
               LEAD_CONTACT_IND,
               ACCT_NUM,
               CIF_ACT_COD_CUS_RAW,
               CONS_INFO_IND,
               CBRS_SEGMENT,
               CBRS_TERMS_FREQ,
               ECOA_CODE_CUS,
               SOURCE_DB
          FROM ranked
         WHERE dedup_rn = 1
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- V_SHAW_TRANERT_BK1  (structure only; cross-schema uzapp_ad0.cds_acct_bk1)
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.V_SHAW_TRANERT_BK1 AS
      SELECT * FROM (
        SELECT slm.acct_num,
               b.bankruptcy_chapter_7591 AS BANKRUPTCY_CHAPTER,
               b.dismissed_date_7596     AS DISMISSED_DATE,
               b.discharge_date_7595     AS DISCHARGE_DATE,
               b.bankruptcy_date_7582    AS BANKRUPTCY_DATE
          FROM app_int.SHAW_LOAN_MASTER slm
         INNER JOIN (
                 SELECT acct_num,
                        bankruptcy_chapter_7591,
                        dismissed_date_7596,
                        discharge_date_7595,
                        bankruptcy_date_7582,
                        ROW_NUMBER() OVER (
                          PARTITION BY acct_num
                          ORDER BY bankruptcy_date_7582 DESC
                        ) AS rn
                   FROM uzapp_ad0.cds_acct_bk1
                  WHERE location_code = '100030'
               ) b
            ON slm.acct_num = b.acct_num
           AND b.rn = 1
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- V_SHAW_TRANERT_BK3  (structure only; cross-schema uzapp_ad0.cds_acct_bk3)
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.V_SHAW_TRANERT_BK3 AS
      SELECT * FROM (
        SELECT slm.acct_num,
               b.bankruptcy_chapter_7891 AS BANKRUPTCY_CHAPTER,
               b.dismissed_date_7896     AS DISMISSED_DATE,
               b.discharge_date_7895     AS DISCHARGE_DATE,
               b.bankruptcy_date_7882    AS BANKRUPTCY_DATE
          FROM app_int.SHAW_LOAN_MASTER slm
         INNER JOIN (
                 SELECT acct_num,
                        bankruptcy_chapter_7891,
                        dismissed_date_7896,
                        discharge_date_7895,
                        bankruptcy_date_7882,
                        ROW_NUMBER() OVER (
                          PARTITION BY acct_num
                          ORDER BY bankruptcy_date_7882 DESC
                        ) AS rn
                   FROM uzapp_ad0.cds_acct_bk3
                  WHERE location_code = '100030'
               ) b
            ON slm.acct_num = b.acct_num
           AND b.rn = 1
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- V_SHAW_TRANERT_STATE_PROVINCE  (structure only; cross-schema uzapp_ad0.*)
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.V_SHAW_TRANERT_STATE_PROVINCE AS
      SELECT * FROM (
        SELECT m.acct_num,
               a.portfolio_id,
               ad.state_province
          FROM app_int.shaw_loan_master m
         INNER JOIN uzapp_ad0.account a
            ON m.acct_num = a.acct_num
         INNER JOIN uzapp_ad0.coll_addr ad
            ON a.portfolio_id = ad.coll_cont_guid
         INNER JOIN uzapp_ad0.coll_addr_role r
            ON r.COLL_CONT_GUID = ad.COLL_CONT_GUID
         WHERE m.chg_off_cd = '1'
           AND ad.dem_external_key NOT LIKE '%AA%'
           AND r.ADDR_ROLE_TYPE = '1'
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- Per-record-type EXPECTED_*_TBL tables (commit 5b1 — simple types)
--------------------------------------------------------------------------------
-- Shape (B), session 7/8. These materialize the per-record-type expected
-- result sets that the L2b SQL-Truth gate compares against the parsed output
-- file. Like the V_SHAW_TRANERT_* helpers above they are app_int-owned CTAS
-- tables (app_int lacks CREATE VIEW; ORA-01031), structure-only here and
-- populated by 10_load/020_refresh_expected.sql.
--
-- 5b1 covers the SIMPLE record types whose Java mapper never returns null
-- (cardinality one_per_driver_row): 32000, 32040, 32075, plus the batch
-- header. The complex types (32005, 32010, 32025) land in 5b2.
--
-- Ground truth for every column (read end-to-end):
--   - config/templates/TranertMapper (1).java  (getTranertCus*, getTranertHeader)
--   - config/templates/DAOOperations (2).java   (result-set column names)
--   - the 13-correction list comment on issue #17
--
-- The driver row set (CHG_OFF_CD = '1' AND TRUNC(M_DATE_PAID_OFF) =
-- TRUNC(BATCH_DATE)) is single-sourced from V_SHAW_TRANERT_DRIVER, which
-- projects SHAW_LOAN_MASTER.* (so every driver column the mappers read —
-- ACCT_NUM, BATCH_DATE, DTE_LAST_RUN, M_FIRST_DELQ_DT, M_TRW_COMMENTS,
-- M_CHARGE_OFF_AMT, M_ACB_COMP_COND_CD, BK, DEPT, M_MISC_CODE2,
-- M_PAYOFF_TRANS, M_DATE_PAID_OFF, M_BANKRUPTCY_DT — is available).
--
-- Each constant column is CAST to an explicit type/width so the CTAS table
-- gets a deterministic column shape (untyped string literals otherwise
-- create ORA-01723-prone zero-length or implicit-width columns under CTAS).
--------------------------------------------------------------------------------


--------------------------------------------------------------------------------
-- EXPECTED_BATCH_HEADER_TBL  (structure only)
--   TranertMapper.getTranertHeader: all columns are fixed constants except
--   ITM_CNT_BRT, which Java sets to the total detail-row count (correction
--   #4 on issue #17; header excluded from its own count).
--
--   5b1 NOTE: ITM_CNT_BRT is the SUM of the per-record-type EXPECTED_*_TBL
--   row counts that EXIST so far (32000 + 32040 + 32075). It is intentionally
--   correct-for-what-is-materialized and will grow as 32005/32010/32025 land
--   in 5b2 (their COUNT(*) terms get added to the refresh in the same commit
--   that creates them). The authoritative whole-file assertion
--   (header.ITM-CNT-BRT == sum(detail_row_counts)) is the comparator's job
--   via the reconciliation YAML (issue #19), not this table.
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.EXPECTED_BATCH_HEADER_TBL AS
      SELECT * FROM (
        SELECT CAST('00040'           AS VARCHAR2(5))  AS BK_NUM_BRT,
               CAST('001'             AS VARCHAR2(3))  AS APP_BRT,
               CAST('BATCH'           AS VARCHAR2(5))  AS TRN_COD_BRT,
               CAST('0000150'         AS VARCHAR2(7))  AS BAT_NUM_BRT,
               CAST('130'             AS VARCHAR2(3))  AS INP_SRC_COD_BRT,
               CAST('32'              AS VARCHAR2(2))  AS BAT_TYP_BRT,
               CAST('SHWCOF'          AS VARCHAR2(8))  AS OPR_ID_BRT,
               CAST('0000001'         AS VARCHAR2(7))  AS ORG_LVL_NUM_1_BRT,
               CAST('0000001'         AS VARCHAR2(7))  AS ORG_LVL_NUM_2_BRT,
               CAST('0000040'         AS VARCHAR2(7))  AS ORG_LVL_NUM_3_BRT,
               CAST('0000000'         AS VARCHAR2(7))  AS ORG_LVL_NUM_4_BRT,
               CAST('0000001'         AS VARCHAR2(7))  AS ORG_LVL_NUM_5_BRT,
               CAST('0000000'         AS VARCHAR2(7))  AS ORG_LVL_NUM_6_BRT,
               CAST('0000000'         AS VARCHAR2(7))  AS ORG_LVL_NUM_7_BRT,
               CAST('0000000'         AS VARCHAR2(7))  AS ORG_LVL_NUM_8_BRT,
               CAST('0000000'         AS VARCHAR2(7))  AS ORG_LVL_NUM_9_BRT,
               CAST('0000000'         AS VARCHAR2(7))  AS ORG_LVL_NUM_10_BRT,
               CAST('0000000'         AS VARCHAR2(7))  AS ORG_LVL_NUM_11_BRT,
               CAST('0000000'         AS VARCHAR2(7))  AS ORG_LVL_NUM_12_BRT,
               CAST(0                 AS NUMBER(9))    AS ITM_CNT_BRT,
               CAST('000000000000.00' AS VARCHAR2(22)) AS DR_CR_AMT_BRT
          FROM DUAL
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- EXPECTED_32000_TBL  (structure only)
--   TranertMapper.getTranertCus32000: pure direct copy + constants.
--   EFF_DAT_ERT = DTE_LAST_RUN when non-null else BATCH_DATE.
--   one_per_driver_row.
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.EXPECTED_32000_TBL AS
      SELECT * FROM (
        SELECT CAST('00040' AS VARCHAR2(5)) AS BK_NUM_ERT,
               CAST('32000' AS VARCHAR2(5)) AS TRN_COD_ERT,
               CAST('001'   AS VARCHAR2(3)) AS APP_ERT,
               d.ACCT_NUM                   AS LN_NUM_ERT,
               NVL(d.DTE_LAST_RUN, d.BATCH_DATE) AS EFF_DAT_ERT,
               CAST('100030' AS VARCHAR2(6)) AS LCT_COD_NEW1
          FROM app_int.V_SHAW_TRANERT_DRIVER d
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- EXPECTED_32040_TBL  (structure only)
--   TranertMapper.getTranertCus32040. one_per_driver_row.
--
--   Fields modelled (Java line refs in TranertMapper (1).java):
--     * BK_NUM_ERT/REF_NUM_ERT/TRN_COD_ERT/APP_ERT — constants (548-551).
--     * EFF_DAT_ERT — DTE_LAST_RUN else BATCH_DATE (552-555).
--     * DAT_DLQ_STR_CBRS — conditional (558-559, correction #10): populated
--       only when M_FIRST_DELQ_DT IS NOT NULL AND M_FIRST_DELQ_DT is NOT
--       after BATCH_DATE (i.e. <= BATCH_DATE). Modelled as a CASE here;
--       the per-field predicate is also declared in the reconciliation YAML.
--     * M2F_CMT_COD_CBRS — M_TRW_COMMENTS when non-null (560-562).
--     * HGH_AMT_DLQ_CBRS — M_CHARGE_OFF_AMT (564).
--     * M2F_CMP_CON_COD_CBRS — M_ACB_COMP_COND_CD when non-null (565-567).
--     * LN_NUM_ERT — ACCT_NUM (568).
--     * PRE_COF_L1_NUM_CBRS — when BK is non-null and length <= 3:
--       LPAD(BK,3,'0') trimmed, then concatenated with ACCT_NUM, trimmed
--       (569-579). Null otherwise.
--     * ACT_TYP_CBRS — three-branch cascade (581-617, corrections #10/#11):
--         (a) M_MISC_CODE2='W' AND DEPT set ->
--             LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT.ACT_TYPE (the post-':' half,
--             IDX3_VARIANT='STD').
--         (b) else if CBRS account_type set ->
--             LKP_VALDO_SHAW_ACT_TYP_UI.ACT_TYP, with the "0"-prefix
--             fallback already encoded as separate ACT_TYP_UI rows, so a
--             single equijoin on the trimmed account_type covers both the
--             unprefixed and "0"-prefixed Java lookups.
--         (c) else if DEPT set -> same as (a).
--       Account type comes from V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY
--       (DAOOperations line 3724: ACCOUNT_TYPE FROM CBRS_TRW_SUMMARY).
--     * CUR_PMT_RTG_CBRS — constant 'L' (622).
--     * PET_DAT_CBRS — M_BANKRUPTCY_DT when M_MISC_CODE2 <> 'W' AND
--       M_BANKRUPTCY_DT is non-null (624-638).
--     * LAS_ACT_STA_CBRS/LAS_DAT_RPT_CBRS/LAS_CMT_COD_CBRS — set together
--       when M_PAYOFF_TRANS ends with 'ST' (case-insensitive) OR M_TRW_COMMENTS
--       trimmed equals 'AU' (640-646): '13', M_DATE_PAID_OFF, 'AU'.
--
--   NOTE on M_MISC_CODE2: DAOOperations sets it to "" (not null) when the
--   source column is null (lines 3493-3495), so the Java does
--   m_misc_code2.trim().equalsIgnoreCase("W"). The SQL mirrors this with
--   TRIM(NVL(M_MISC_CODE2,'')) so a null column never matches 'W'.
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.EXPECTED_32040_TBL AS
      SELECT * FROM (
        SELECT CAST('00040' AS VARCHAR2(5)) AS BK_NUM_ERT,
               CAST(''      AS VARCHAR2(1)) AS REF_NUM_ERT,
               CAST('32040' AS VARCHAR2(5)) AS TRN_COD_ERT,
               CAST('001'   AS VARCHAR2(3)) AS APP_ERT,
               NVL(d.DTE_LAST_RUN, d.BATCH_DATE) AS EFF_DAT_ERT,
               CASE
                 WHEN d.M_FIRST_DELQ_DT IS NOT NULL
                  AND d.M_FIRST_DELQ_DT <= d.BATCH_DATE
                 THEN d.M_FIRST_DELQ_DT
               END                          AS DAT_DLQ_STR_CBRS,
               d.M_TRW_COMMENTS             AS M2F_CMT_COD_CBRS,
               d.M_CHARGE_OFF_AMT           AS HGH_AMT_DLQ_CBRS,
               d.M_ACB_COMP_COND_CD         AS M2F_CMP_CON_COD_CBRS,
               d.ACCT_NUM                   AS LN_NUM_ERT,
               CASE
                 WHEN d.BK IS NOT NULL AND LENGTH(d.BK) <= 3
                 THEN TRIM(TRIM(LPAD(d.BK, 3, '0')) || d.ACCT_NUM)
               END                          AS PRE_COF_L1_NUM_CBRS,
               CAST(
                 CASE
                   -- branch (a): mMiscCode2 = 'W' and dept set
                   WHEN TRIM(NVL(d.M_MISC_CODE2, '')) = 'W'
                    AND d.DEPT IS NOT NULL
                   THEN led_w.ACT_TYPE
                   -- branch (b): CBRS account_type set
                   WHEN cbrs.ACCOUNT_TYPE IS NOT NULL
                   THEN ui.ACT_TYP
                   -- branch (c): dept fallback
                   WHEN d.DEPT IS NOT NULL
                   THEN led_d.ACT_TYPE
                 END AS VARCHAR2(20))       AS ACT_TYP_CBRS,
               CAST('L' AS VARCHAR2(1))     AS CUR_PMT_RTG_CBRS,
               CASE
                 WHEN TRIM(NVL(d.M_MISC_CODE2, '')) <> 'W'
                  AND d.M_BANKRUPTCY_DT IS NOT NULL
                 THEN d.M_BANKRUPTCY_DT
               END                          AS PET_DAT_CBRS,
               CASE
                 WHEN (d.M_PAYOFF_TRANS IS NOT NULL
                       AND UPPER(TRIM(d.M_PAYOFF_TRANS)) LIKE '%ST')
                   OR (d.M_TRW_COMMENTS IS NOT NULL
                       AND TRIM(d.M_TRW_COMMENTS) <> ''
                       AND UPPER(TRIM(d.M_TRW_COMMENTS)) = 'AU')
                 THEN '13'
               END                          AS LAS_ACT_STA_CBRS,
               CASE
                 WHEN (d.M_PAYOFF_TRANS IS NOT NULL
                       AND UPPER(TRIM(d.M_PAYOFF_TRANS)) LIKE '%ST')
                   OR (d.M_TRW_COMMENTS IS NOT NULL
                       AND TRIM(d.M_TRW_COMMENTS) <> ''
                       AND UPPER(TRIM(d.M_TRW_COMMENTS)) = 'AU')
                 THEN d.M_DATE_PAID_OFF
               END                          AS LAS_DAT_RPT_CBRS,
               CASE
                 WHEN (d.M_PAYOFF_TRANS IS NOT NULL
                       AND UPPER(TRIM(d.M_PAYOFF_TRANS)) LIKE '%ST')
                   OR (d.M_TRW_COMMENTS IS NOT NULL
                       AND TRIM(d.M_TRW_COMMENTS) <> ''
                       AND UPPER(TRIM(d.M_TRW_COMMENTS)) = 'AU')
                 THEN 'AU'
               END                          AS LAS_CMT_COD_CBRS
          FROM app_int.V_SHAW_TRANERT_DRIVER d
          -- The Java reads CBRS as a Map<acctNum, CBRSSummaryData> (one
          -- value per account: cbrAccountMap.get(acctNum)). The summary
          -- table holds many rows per account that differ only in
          -- ACCOUNT_STATUS/PAST_DUE (which 32040 does NOT read); ACCOUNT_TYPE
          -- is constant within an account. Join to a per-account-deduped
          -- projection of the only column 32040 consumes so the result stays
          -- one_per_driver_row instead of fanning out on the duplicates.
          LEFT JOIN (SELECT DISTINCT ACCOUNT_NUMBER, ACCOUNT_TYPE
                       FROM app_int.V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY) cbrs
                 ON cbrs.ACCOUNT_NUMBER = d.ACCT_NUM
          LEFT JOIN app_int.LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT led_w
                 ON led_w.DEPT = TRIM(d.DEPT)
                AND led_w.IDX3_VARIANT = 'STD'
          LEFT JOIN app_int.LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT led_d
                 ON led_d.DEPT = TRIM(d.DEPT)
                AND led_d.IDX3_VARIANT = 'STD'
          LEFT JOIN app_int.LKP_VALDO_SHAW_ACT_TYP_UI ui
                 ON ui.ACCOUNT_TYPE = TRIM(cbrs.ACCOUNT_TYPE)
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- EXPECTED_32075_TBL  (structure only)
--   TranertMapper.getTranertCus32075: constants + RCF_DUE_REC from the
--   cost map (tranertCost32075Map), which is V_SHAW_TRANERT_COST_MERGED
--   keyed by ACCT_NUM. one_per_driver_row.
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.EXPECTED_32075_TBL AS
      SELECT * FROM (
        SELECT CAST('00040' AS VARCHAR2(5)) AS BK_NUM_ERT,
               CAST(''      AS VARCHAR2(1)) AS REF_NUM_ERT,
               CAST('32075' AS VARCHAR2(5)) AS TRN_COD_ERT,
               CAST('001'   AS VARCHAR2(3)) AS APP_ERT,
               d.ACCT_NUM                   AS LN_NUM_ERT,
               NVL(d.DTE_LAST_RUN, d.BATCH_DATE) AS EFF_DAT_ERT,
               CAST('GNR' AS VARCHAR2(3))   AS RCF_REF_NUM_REC,
               cm.RCF_DUE_REC               AS RCF_DUE_REC,
               CAST(0   AS NUMBER(1))       AS RCF_ASE_COD_REC,
               CAST(0   AS NUMBER(1))       AS RCF_INT_IND_REC,
               CAST('Y' AS VARCHAR2(1))     AS EXP_PYF_IND_REC,
               CAST('0' AS VARCHAR2(1))     AS RCF_ICR_COD_REC
          FROM app_int.V_SHAW_TRANERT_DRIVER d
          LEFT JOIN app_int.V_SHAW_TRANERT_COST_MERGED cm
                 ON cm.ACCT_NUM = d.ACCT_NUM
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- Per-record-type EXPECTED_*_TBL tables (commit 5b2a — complex type 32005)
--------------------------------------------------------------------------------
-- 32005 (CUS) is the per-contact detail record. Unlike the 5b1 simple types
-- it is many_per_driver_row: one row per non-skipped contact of each driver
-- account, composite key (ACCT_NUM, CONTACT_ID). It composes the
-- V_SHAW_TRANERT_CONTACTS_MERGED helper (the APPS u APP_INT contact merge),
-- the bk1/bk3 chapter helpers, the LOANS_NAME helper, and the
-- name-relation -> cif_act_code lookup.
--
-- Ground truth: TranertMapper (1).java getTranertCus32005 (L89-167) and the
-- two private chapter-map helpers extractedDismissedAndDischargeDateIsNull
-- (7/07->A, 11->B, 12->C, 13->D) and
-- extractedDismissedIsNullAndDischargeDateIsNotNull (7/07->E, 11->F, 12->G,
-- 13->H), plus the 13-correction list items #2 and #12 on issue #17.
--
-- TODO(valdo-gap): two ContactAccountLoan fields the Java mapper consumes are
--   NOT exposed by V_SHAW_TRANERT_CONTACTS_MERGED (it carries one row per
--   contact with no per-account ref-num/primary ordinal):
--     * cifRefNum         -> the literal output column CIF_REF_NUM_CUS
--                            (Java: cifRefNum + "", L124), and
--     * primary_ref_flag  -> the "no primary, secondary acts as primary"
--                            short-circuit cifRefNum == 997 && !primary_ref_flag
--                            -> CIF_ACT_COD_CUS = 'P' (Java L116-117).
--   Modelling these faithfully requires reproducing the Java caller's
--   per-account contact-iteration ordinal assignment, which is stateful and
--   not derivable from the merged-contacts row set alone. Per the session-9
--   decision (option A) these are deferred:
--     * CIF_REF_NUM_CUS is OMITTED from this table (no column emitted), and
--     * CIF_ACT_COD_CUS is emitted from the name-relation lookup WITHOUT the
--       997/'P' short-circuit.
--   Both must be flagged regression_only: true in the issue #19 reconciliation
--   YAML so L2b does not compare them; L3 baseline diff covers them. A follow
--   -up may extend CONTACTS_MERGED with CIF_REF_NUM + PRIMARY_REF_FLAG ordinals
--   (separate MR + the contact-iteration order confirmed against DAOOperations).
--
-- Cardinality note: CONTACTS_MERGED is already deduped to one row per
-- (ACCT_NUM, CONTACT_ID) by the helper's ROW_NUMBER() partition, so joining
-- the driver to it does NOT fan out beyond the intended per-contact grain.
-- The skip predicate nameRelationship IN {M, N, NULL} (correction #2) is
-- applied as a WHERE filter so skipped contacts emit no row (matching the
-- Java's `return null`).
--
-- Each constant column is CAST to an explicit type/width (same rationale as
-- the 5b1 tables) so the CTAS gives a deterministic column shape.
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.EXPECTED_32005_TBL AS
      SELECT * FROM (
        SELECT CAST('00040' AS VARCHAR2(5)) AS BK_NUM_ERT,
               CAST(''      AS VARCHAR2(1)) AS REF_NUM_ERT,
               CAST('32005' AS VARCHAR2(5)) AS TRN_COD_ERT,
               CAST('001'   AS VARCHAR2(3)) AS APP_ERT,
               d.ACCT_NUM                   AS LN_NUM_ERT,
               NVL(d.DTE_LAST_RUN, d.BATCH_DATE) AS EFF_DAT_ERT,
               -- CONTACT_ID is left-padded to width 15 (Java L122:
               -- StringUtils.leftPad(contactId, 15, "0")).
               CAST(LPAD(c.CONTACT_ID, 15, '0') AS VARCHAR2(15)) AS CONTACT_ID,
               c.CONTACT_ID                 AS CIF_ACT_NUM_CUS,
               c.ACCT_NUM                   AS ACCT_NUM,
               TRIM(c.NAME_RELATIONSHIP)    AS NAME_RELATIONSHIP,
               -- CIF_ACT_COD_CUS: name-relation lookup ONLY (the 997/'P'
               -- short-circuit is deferred; see TODO(valdo-gap) above).
               CAST(cif.CIF_ACT_COD AS VARCHAR2(5)) AS CIF_ACT_COD_CUS,
               CAST('1' AS VARCHAR2(1))     AS RESPONSIBLE_PARTY,
               -- LEAD_CONTACT_IND = '1' when nameRelationship = 'A' else '0'
               -- (Java L101-104).
               CAST(
                 CASE WHEN UPPER(TRIM(c.NAME_RELATIONSHIP)) = 'A'
                      THEN '1' ELSE '0' END AS VARCHAR2(1)) AS LEAD_CONTACT_IND,
               -- ECOA_CODE_CUS (Java L45-51): base = contact ECOA; overridden
               -- to 'W' when mMiscCode2 = 'W' OR when base is null/blank.
               CAST(
                 CASE
                   WHEN TRIM(NVL(d.M_MISC_CODE2, '')) = 'W' THEN 'W'
                   WHEN c.ECOA_CODE_CUS IS NULL
                     OR TRIM(c.ECOA_CODE_CUS) IS NULL THEN 'W'
                   ELSE c.ECOA_CODE_CUS
                 END AS VARCHAR2(10))       AS ECOA_CODE_CUS,
               CAST('' AS VARCHAR2(1))      AS LAST_CHEX_RECORDED_DATE_CUS,
               CAST('A' AS VARCHAR2(1))     AS ACTION_CODE,
               CAST('100030' AS VARCHAR2(6)) AS LOCATION_CODE,
               -- CAS_ADDRESS_IND = 'N' when raw contact cif_act_cod = 'N',
               -- else 'Y' (Java L88-90).
               CAST(
                 CASE WHEN UPPER(TRIM(c.CIF_ACT_COD_CUS_RAW)) = 'N'
                      THEN 'N' ELSE 'Y' END AS VARCHAR2(1)) AS CAS_ADDRESS_IND,
               CAST('LS'  AS VARCHAR2(2))   AS EXTERNAL_SYSTEM_ID,
               CAST('USD' AS VARCHAR2(3))   AS PREFERRED_CURRENCY,
               -- CIF_CBR_RPT_IND_CUS (Java L97-115): default 'Y', flipped to
               -- 'N' under any of the five suppression branches.
               CAST(
                 CASE
                   WHEN cbrs.ACCOUNT_STATUS IS NULL
                     OR TRIM(cbrs.ACCOUNT_STATUS) IS NULL THEN 'N'
                   WHEN d.M_BANKRUPTCY_DT IS NOT NULL
                    AND TRIM(cbrs.ACCOUNT_STATUS) <> '11'
                    AND (cbrs.PAST_DUE IS NULL OR cbrs.PAST_DUE = 0) THEN 'N'
                   WHEN cbrs.PORTFOLIO_TYPE IS NOT NULL
                    AND UPPER(TRIM(cbrs.PORTFOLIO_TYPE)) = 'M' THEN 'N'
                   WHEN d.M_FIRST_DELQ_DT IS NULL
                     OR d.M_FIRST_DELQ_DT > d.BATCH_DATE
                     OR (TRIM(NVL(d.M_MISC_CODE2, '')) = 'W') THEN 'N'
                   WHEN (d.M_PAYOFF_TRANS IS NOT NULL
                         AND UPPER(TRIM(d.M_PAYOFF_TRANS)) LIKE '%ST')
                     OR (d.M_TRW_COMMENTS IS NOT NULL
                         AND TRIM(d.M_TRW_COMMENTS) <> ''
                         AND UPPER(TRIM(d.M_TRW_COMMENTS)) = 'AU') THEN 'N'
                   ELSE 'Y'
                 END AS VARCHAR2(1))        AS CIF_CBR_RPT_IND_CUS,
               d.M_DATE_PAID_OFF            AS LAST_BUREAU_RECORDED_DATE_CUS,
               -- CIF_CSM_INF_IND_CUS (Java L98-160, correction #12): a
               -- multi-source CASE chain. Branch order is significant.
               CAST(
                 CASE
                   -- (1) mMiscCode2 = 'W' -> loanAccountNames map value.
                   WHEN TRIM(NVL(d.M_MISC_CODE2, '')) = 'W'
                     THEN ln.KEYED_VALUE
                   -- (2) consInfoInd non-blank -> consInfoInd, mapped to 'Q'
                   --     when it is one of I,J,K,L,M,N,O,P,Z.
                   WHEN c.CONS_INFO_IND IS NOT NULL
                    AND TRIM(c.CONS_INFO_IND) IS NOT NULL THEN
                     CASE
                       WHEN INSTR('I,J,K,L,M,N,O,P,Z', TRIM(c.CONS_INFO_IND)) > 0
                         THEN 'Q'
                       ELSE c.CONS_INFO_IND
                     END
                   -- (3) else bk1 (rel 'A') / bk3 (rel 'B') chapter cascade.
                   WHEN UPPER(TRIM(c.NAME_RELATIONSHIP)) = 'A'
                    AND bk1.ACCT_NUM IS NOT NULL THEN
                     CASE
                       WHEN bk1.DISMISSED_DATE IS NOT NULL THEN 'Q'
                       WHEN bk1.DISMISSED_DATE IS NULL
                        AND bk1.DISCHARGE_DATE IS NULL THEN
                         CASE TRIM(bk1.BANKRUPTCY_CHAPTER)
                           WHEN '7'  THEN 'A' WHEN '07' THEN 'A'
                           WHEN '11' THEN 'B' WHEN '12' THEN 'C'
                           WHEN '13' THEN 'D' ELSE '' END
                       WHEN bk1.DISMISSED_DATE IS NULL
                        AND bk1.DISCHARGE_DATE IS NOT NULL THEN
                         CASE TRIM(bk1.BANKRUPTCY_CHAPTER)
                           WHEN '7'  THEN 'E' WHEN '07' THEN 'E'
                           WHEN '11' THEN 'F' WHEN '12' THEN 'G'
                           WHEN '13' THEN 'H' ELSE '' END
                     END
                   WHEN UPPER(TRIM(c.NAME_RELATIONSHIP)) = 'B'
                    AND bk3.ACCT_NUM IS NOT NULL THEN
                     CASE
                       WHEN bk3.DISMISSED_DATE IS NOT NULL THEN 'Q'
                       WHEN bk3.DISMISSED_DATE IS NULL
                        AND bk3.DISCHARGE_DATE IS NULL THEN
                         CASE TRIM(bk3.BANKRUPTCY_CHAPTER)
                           WHEN '7'  THEN 'A' WHEN '07' THEN 'A'
                           WHEN '11' THEN 'B' WHEN '12' THEN 'C'
                           WHEN '13' THEN 'D' ELSE '' END
                       WHEN bk3.DISMISSED_DATE IS NULL
                        AND bk3.DISCHARGE_DATE IS NOT NULL THEN
                         CASE TRIM(bk3.BANKRUPTCY_CHAPTER)
                           WHEN '7'  THEN 'E' WHEN '07' THEN 'E'
                           WHEN '11' THEN 'F' WHEN '12' THEN 'G'
                           WHEN '13' THEN 'H' ELSE '' END
                     END
                 END AS VARCHAR2(20))       AS CIF_CSM_INF_IND_CUS,
               -- DAT_BKY_REC_CUS (Java L82-85, 121, 130, 142): default is the
               -- account bankruptcy date; inside the bk both-dates-null and
               -- discharge-not-null chapter branches it is overridden to the
               -- bk row's own bankruptcy date.
               CASE
                 WHEN TRIM(NVL(d.M_MISC_CODE2, '')) <> 'W'
                  AND (c.CONS_INFO_IND IS NULL OR TRIM(c.CONS_INFO_IND) IS NULL)
                  AND UPPER(TRIM(c.NAME_RELATIONSHIP)) = 'A'
                  AND bk1.ACCT_NUM IS NOT NULL
                  AND bk1.DISMISSED_DATE IS NULL
                  AND bk1.BANKRUPTCY_CHAPTER IS NOT NULL
                   THEN bk1.BANKRUPTCY_DATE
                 WHEN TRIM(NVL(d.M_MISC_CODE2, '')) <> 'W'
                  AND (c.CONS_INFO_IND IS NULL OR TRIM(c.CONS_INFO_IND) IS NULL)
                  AND UPPER(TRIM(c.NAME_RELATIONSHIP)) = 'B'
                  AND bk3.ACCT_NUM IS NOT NULL
                  AND bk3.DISMISSED_DATE IS NULL
                  AND bk3.BANKRUPTCY_CHAPTER IS NOT NULL
                   THEN bk3.BANKRUPTCY_DATE
                 ELSE d.M_BANKRUPTCY_DT
               END                          AS DAT_BKY_REC_CUS
          FROM app_int.V_SHAW_TRANERT_DRIVER d
          JOIN app_int.V_SHAW_TRANERT_CONTACTS_MERGED c
                 ON c.ACCT_NUM = d.ACCT_NUM
          LEFT JOIN app_int.LKP_VALDO_SHAW_CIF_ACT_CODE_BY_NAME_RELATION cif
                 ON cif.NAME_RELATIONSHIP = TRIM(c.NAME_RELATIONSHIP)
          -- loanAccountNames.get(acctNum) is a single map value per account.
          -- KEYED_VALUE is '<acct>-<na_cons_info_ind>'; pick one row per
          -- account deterministically so the join stays per-contact-grain.
          LEFT JOIN (SELECT ACCT_NUM, KEYED_VALUE
                       FROM (SELECT SUBSTR(KEYED_VALUE, 1,
                                           INSTR(KEYED_VALUE, '-') - 1) AS ACCT_NUM,
                                    KEYED_VALUE,
                                    ROW_NUMBER() OVER (
                                      PARTITION BY SUBSTR(KEYED_VALUE, 1,
                                                   INSTR(KEYED_VALUE, '-') - 1)
                                      ORDER BY KEYED_VALUE
                                    ) AS rn
                               FROM app_int.V_SHAW_TRANERT_LOANS_NAME)
                      WHERE rn = 1) ln
                 ON ln.ACCT_NUM = d.ACCT_NUM
          -- Per-account CBRS projection (one row per account, mirroring the
          -- Java Map<acctNum, CBRSSummaryData>). The summary holds many rows
          -- per account; pick the first deterministically so the join does
          -- not fan out the per-contact grain. ACCOUNT_STATUS/PAST_DUE can
          -- differ across an account's summary rows, but the Java likewise
          -- consumes a single map value per account, so a deterministic pick
          -- is faithful to the source behaviour.
          LEFT JOIN (SELECT ACCOUNT_NUMBER, ACCOUNT_STATUS,
                            PORTFOLIO_TYPE, PAST_DUE
                       FROM (SELECT ACCOUNT_NUMBER, ACCOUNT_STATUS,
                                    PORTFOLIO_TYPE, PAST_DUE,
                                    ROW_NUMBER() OVER (
                                      PARTITION BY ACCOUNT_NUMBER
                                      ORDER BY ACCOUNT_STATUS, PAST_DUE
                                    ) AS rn
                               FROM app_int.V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY)
                      WHERE rn = 1) cbrs
                 ON cbrs.ACCOUNT_NUMBER = d.ACCT_NUM
          LEFT JOIN app_int.V_SHAW_TRANERT_BK1 bk1
                 ON bk1.ACCT_NUM = d.ACCT_NUM
          LEFT JOIN app_int.V_SHAW_TRANERT_BK3 bk3
                 ON bk3.ACCT_NUM = d.ACCT_NUM
          -- Skip contacts whose name relationship is M, N, or null
          -- (Java L78-80: return null). Applied as a filter so no row emits.
         WHERE c.NAME_RELATIONSHIP IS NOT NULL
           AND UPPER(TRIM(c.NAME_RELATIONSHIP)) NOT IN ('M', 'N')
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- Per-record-type EXPECTED_*_TBL tables (commit 5b2b — complex types
-- 32010 + 32025)
--------------------------------------------------------------------------------
-- EXPECTED_32010_TBL  (structure only)
--   TranertMapper.getTranertCus32010 (L259-434). Cardinality
--   zero_or_one_per_driver_row: the mapper returns null when
--   chgOffCd <> '1' (Java L323-324). The driver row set is already
--   filtered to CHG_OFF_CD = '1' (V_SHAW_TRANERT_DRIVER), so on the driver
--   every row emits; the suppression predicate is encoded for completeness
--   and declared in the issue #19 reconciliation YAML.
--
--   Fields modelled (Java line refs in TranertMapper (1).java):
--     * BK_NUM/REF_NUM/TRN_COD/APP/EFF_DAT — constants + dteLastRun/batchDate.
--     * OGL_CONTRACT_DAT_ORI = noteDte (L285).
--     * OGL_NTE_AMT_ORI = mAcbChargeOffAmt (L286).
--     * OGL_COF_INT_AMT_ORI = mPoIntPay (L287).
--     * CNV_ACT_NUM_ORI = mAuxiliarySearch (L288).
--     * INT_RT_ORI = constant '.000000' (L290).
--     * OGL_TRM_ORI — orgTerm > '999' (string compare) -> 999; else the
--       numeric orgTerm when non-blank (L292-300). Null when blank.
--     * OGL_MAT_DAT_ORI = mFinalDate else matDte (L302-306).
--     * OGL_CONTRACT_AMT_ORI = org when org <> 0, else mChargeOffAmt
--       (L308-311; correction #5 CASE fallback).
--     * OGL_PORTFOLIO_TYP_ORI = 'I', or 'C' when typeX in {7,8} (L313-319).
--     * COF_CDN_IND_ORI = constant '1' (L321).
--     * OGL_NTE_DAT_ORI = mDatePaidOff, set only in the chgOffCd='1' branch
--       (L323-326; correction #6 conditional). On the driver this is always
--       populated.
--     * OGL_INT_RT_ORI = rateP / 100 rounded to 4dp (L328-335). Null when
--       rateP blank.
--     * REP_TYP_ORI — default '001'; prinSch lookup
--       (LKP_VALDO_SHAW_REP_TYPE_ORI_BY_PRIN_SCH) when prinSch non-blank;
--       overridden by the BASE-segment termsFreq lookup
--       (LKP_VALDO_SHAW_REP_TYP_URI_BY_TERMS_FREQ) when a contact has
--       segment='BASE' and non-blank termsFreq; but forced back to '001'
--       when mMiscCode2='W' (L337-360). Branch order: W wins over BASE wins
--       over prinSch wins over default.
--     * HGH_BAL_ORI = mAcbHighBalance when typeX in {7,8} (L362-364;
--       correction #8 conditional).
--     * COF_REA_COD_ORI — last 2 chars of mPayoffTrans; '0'+value when
--       length 1 (L366-372; correction #7). Null when blank.
--     * ST_COD_ORI — leg A ONLY: LKP_VALDO_SHAW_LOAN_MASTER_STATE_CD_BY_BK
--       keyed by BK (L374-385). The leg-B DAOOperations.stateProvinceMap
--       fallback (cross-run JVM state) is NOT reproduced and is flagged
--       regression_only in the #19 YAML (correction #13; session-9 decision).
--     * DAT_INT_ACR_TO_ORI = mDatePaidOff (L390).
--     * ACT_STA_ORI = constant '0' (L392).
--     * PCOF_DAT_LAS_PMT_ORI = mBbtLstPaymntDte (L394).
--     * PCOF_PMT_AMT_LAS_ORI = mBbtLstPaymntAmt; 1 when null or <= 0
--       (L396-400).
--     * DAT_LAS_STM_ORI = mCycleBegDte - 1 day when mCycleBegDte set
--       (L402-403).
--     * OGL_PMT_AMT_ORI = payment (L405/L431).
--     * LN_TYP_ORI — idx3='AAA' -> LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT
--       (IDX3_VARIANT='AAA') LOAN_TYPE as int; else the STD-variant
--       LOAN_TYPE (already the pre-split [0] half) as int; else 0
--       (L408-426).
--     * DAT_ACT_CLS_TO_ATY_ORI = cbrs.dateClosed when set, else mDatePaidOff
--       (L266-272).
--
--   Joins deduped to one row per account (CBRS map, BASE-segment contact,
--   the two ledger-variant lookups) so the result stays
--   zero_or_one_per_driver_row.
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.EXPECTED_32010_TBL AS
      SELECT * FROM (
        SELECT CAST('00040' AS VARCHAR2(5)) AS BK_NUM_ERT,
               CAST(''      AS VARCHAR2(1)) AS REF_NUM_ERT,
               CAST('32010' AS VARCHAR2(5)) AS TRN_COD_ERT,
               CAST('001'   AS VARCHAR2(3)) AS APP_ERT,
               d.ACCT_NUM                   AS LN_NUM_ERT,
               NVL(d.DTE_LAST_RUN, d.BATCH_DATE) AS EFF_DAT_ERT,
               d.NOTE_DTE                   AS OGL_CONTRACT_DAT_ORI,
               d.M_ACB_CHARGE_OFF_AMT       AS OGL_NTE_AMT_ORI,
               d.M_PO_INT_PAY               AS OGL_COF_INT_AMT_ORI,
               d.M_AUXILIARY_SEARCH         AS CNV_ACT_NUM_ORI,
               CAST('.000000' AS VARCHAR2(10)) AS INT_RT_ORI,
               CASE
                 WHEN d.ORG_TERM IS NOT NULL
                  AND TRIM(d.ORG_TERM) > '999' THEN 999
                 WHEN d.ORG_TERM IS NOT NULL
                  AND LENGTH(TRIM(d.ORG_TERM)) > 0
                  AND REGEXP_LIKE(TRIM(d.ORG_TERM), '^[0-9]+$')
                   THEN TO_NUMBER(TRIM(d.ORG_TERM))
               END                          AS OGL_TRM_ORI,
               NVL(d.M_FINAL_DATE, d.MAT_DTE) AS OGL_MAT_DAT_ORI,
               CASE
                 WHEN d.ORG IS NOT NULL AND d.ORG <> 0 THEN d.ORG
                 ELSE d.M_CHARGE_OFF_AMT
               END                          AS OGL_CONTRACT_AMT_ORI,
               CAST(
                 CASE
                   WHEN TRIM(d.TYPE_X) IN ('7', '8') THEN 'C'
                   ELSE 'I'
                 END AS VARCHAR2(1))        AS OGL_PORTFOLIO_TYP_ORI,
               CAST('1' AS VARCHAR2(1))     AS COF_CDN_IND_ORI,
               CASE
                 WHEN TRIM(d.CHG_OFF_CD) = '1' THEN d.M_DATE_PAID_OFF
               END                          AS OGL_NTE_DAT_ORI,
               CASE
                 WHEN d.RATE_P IS NOT NULL
                  AND LENGTH(TRIM(d.RATE_P)) > 0
                  AND REGEXP_LIKE(TRIM(d.RATE_P), '^[0-9]*\.?[0-9]+$')
                   THEN ROUND(TO_NUMBER(TRIM(d.RATE_P)) / 100, 4)
               END                          AS OGL_INT_RT_ORI,
               CAST(
                 CASE
                   WHEN TRIM(NVL(d.M_MISC_CODE2, '')) = 'W' THEN '001'
                   WHEN base_ct.REP_TYP_URI IS NOT NULL
                     THEN base_ct.REP_TYP_URI
                   WHEN d.PRIN_SCH IS NOT NULL
                    AND LENGTH(TRIM(d.PRIN_SCH)) > 0
                    AND rep_ps.REP_TYP_ORI IS NOT NULL
                     THEN rep_ps.REP_TYP_ORI
                   ELSE '001'
                 END AS VARCHAR2(10))       AS REP_TYP_ORI,
               CASE
                 WHEN TRIM(d.TYPE_X) IN ('7', '8') THEN d.M_ACB_HIGH_BALANCE
               END                          AS HGH_BAL_ORI,
               CAST(
                 CASE
                   WHEN d.M_PAYOFF_TRANS IS NOT NULL
                    AND LENGTH(TRIM(d.M_PAYOFF_TRANS)) >= 2
                     THEN SUBSTR(TRIM(d.M_PAYOFF_TRANS),
                                 LENGTH(TRIM(d.M_PAYOFF_TRANS)) - 1)
                   WHEN d.M_PAYOFF_TRANS IS NOT NULL
                    AND LENGTH(TRIM(d.M_PAYOFF_TRANS)) = 1
                     THEN '0' || TRIM(d.M_PAYOFF_TRANS)
                 END AS VARCHAR2(2))        AS COF_REA_COD_ORI,
               -- Leg A only (BK property lookup). Leg B (stateProvinceMap)
               -- is regression_only; see header comment.
               CAST(st_bk.STATE_CD AS VARCHAR2(10)) AS ST_COD_ORI,
               d.M_DATE_PAID_OFF            AS DAT_INT_ACR_TO_ORI,
               CAST('0' AS VARCHAR2(1))     AS ACT_STA_ORI,
               d.M_BBT_LST_PAYMNT_DTE       AS PCOF_DAT_LAS_PMT_ORI,
               CASE
                 WHEN d.M_BBT_LST_PAYMNT_AMT IS NULL
                   OR d.M_BBT_LST_PAYMNT_AMT <= 0 THEN 1
                 ELSE d.M_BBT_LST_PAYMNT_AMT
               END                          AS PCOF_PMT_AMT_LAS_ORI,
               CASE
                 WHEN d.M_CYCLE_BEG_DTE IS NOT NULL
                   THEN d.M_CYCLE_BEG_DTE - 1
               END                          AS DAT_LAS_STM_ORI,
               d.PAYMENT                    AS OGL_PMT_AMT_ORI,
               CAST(
                 CASE
                   WHEN d.M_USER_IDX3 IS NOT NULL
                    AND UPPER(TRIM(d.M_USER_IDX3)) = 'AAA'
                    AND led_aaa.LOAN_TYPE IS NOT NULL
                    AND REGEXP_LIKE(TRIM(led_aaa.LOAN_TYPE), '^[0-9]+$')
                     THEN TO_NUMBER(TRIM(led_aaa.LOAN_TYPE))
                   WHEN (d.M_USER_IDX3 IS NULL
                         OR UPPER(TRIM(d.M_USER_IDX3)) <> 'AAA')
                    AND led_std.LOAN_TYPE IS NOT NULL
                    AND REGEXP_LIKE(TRIM(led_std.LOAN_TYPE), '^[0-9]+$')
                     THEN TO_NUMBER(TRIM(led_std.LOAN_TYPE))
                   ELSE 0
                 END AS NUMBER(10))         AS LN_TYP_ORI,
               NVL(cbrs.DATE_CLOSED, d.M_DATE_PAID_OFF) AS DAT_ACT_CLS_TO_ATY_ORI
          FROM app_int.V_SHAW_TRANERT_DRIVER d
          -- prinSch -> rep_typ_ori
          LEFT JOIN app_int.LKP_VALDO_SHAW_REP_TYPE_ORI_BY_PRIN_SCH rep_ps
                 ON rep_ps.PRIN_SCH = TRIM(d.PRIN_SCH)
          -- BASE-segment contact termsFreq override -> rep_typ_uri.
          -- One row per account: first BASE contact with non-blank termsFreq.
          LEFT JOIN (
            SELECT ACCT_NUM, REP_TYP_URI
              FROM (
                SELECT cm.ACCT_NUM,
                       uri.REP_TYP_URI,
                       ROW_NUMBER() OVER (
                         PARTITION BY cm.ACCT_NUM
                         ORDER BY cm.CONTACT_ID
                       ) AS rn
                  FROM app_int.V_SHAW_TRANERT_CONTACTS_MERGED cm
                  JOIN app_int.LKP_VALDO_SHAW_REP_TYP_URI_BY_TERMS_FREQ uri
                    ON uri.TERMS_FREQ = TRIM(cm.CBRS_TERMS_FREQ)
                 WHERE cm.CBRS_SEGMENT IS NOT NULL
                   AND TRIM(cm.CBRS_SEGMENT) = 'BASE'
                   AND cm.CBRS_TERMS_FREQ IS NOT NULL
                   AND TRIM(cm.CBRS_TERMS_FREQ) IS NOT NULL
              )
             WHERE rn = 1
          ) base_ct
                 ON base_ct.ACCT_NUM = d.ACCT_NUM
          -- BK -> state code (leg A only)
          LEFT JOIN app_int.LKP_VALDO_SHAW_LOAN_MASTER_STATE_CD_BY_BK st_bk
                 ON st_bk.BK = TRIM(d.BK)
          -- dept -> ledger (idx3 AAA variant)
          LEFT JOIN app_int.LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT led_aaa
                 ON led_aaa.DEPT = TRIM(d.DEPT)
                AND led_aaa.IDX3_VARIANT = 'AAA'
          -- dept -> ledger (standard variant; LOAN_TYPE is the pre-split half)
          LEFT JOIN app_int.LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT led_std
                 ON led_std.DEPT = TRIM(d.DEPT)
                AND led_std.IDX3_VARIANT = 'STD'
          -- CBRS dateClosed (one row per account)
          LEFT JOIN (SELECT ACCOUNT_NUMBER, DATE_CLOSED
                       FROM (SELECT ACCOUNT_NUMBER, DATE_CLOSED,
                                    ROW_NUMBER() OVER (
                                      PARTITION BY ACCOUNT_NUMBER
                                      ORDER BY DATE_CLOSED
                                    ) AS rn
                               FROM app_int.V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY)
                      WHERE rn = 1) cbrs
                 ON cbrs.ACCOUNT_NUMBER = d.ACCT_NUM
         WHERE TRIM(d.CHG_OFF_CD) = '1'
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- EXPECTED_32025_TBL  (structure only)
--   TranertMapper.getTranertCus32025 (L436-540). one_per_driver_row
--   (the mapper never returns null).
--
--   Fields modelled (Java line refs):
--     * BK_NUM/REF_NUM/TRN_COD/APP/LN_NUM/EFF_DAT — constants + acctNum +
--       dteLastRun/batchDate.
--     * LGL_STA_COD_COD — mPayoffTrans ends 'ST' OR mTrwComments='AU' -> 'STL';
--       else chgOffCd map R/P/N->'RPO', B/J->'B06', F->'FCL', X->'PRP'
--       (L451-466).
--     * RPO_COD_COD — 1 when chgOffCd in {X,R,P,N}, else 0 (L445, L470-472).
--     * DAT_LAS_RPO_COD — when chgOffCd in {X,R,P,N}: mRepoDte else
--       mDatePaidOff (L463-469).
--     * DUE_DAT_DAY_COD — day-of-month of dueDteI else dueDteP, default 1
--       (L475-480).
--     * ORG_LVL_NUM1..3,5,7..12_COD — constants (L482-486, L501-507).
--     * ORG_LVL_NUM4_COD — default '0000102'; sapCenter5 lookup
--       (LKP_VALDO_SHAW_ORG_LEVEL4_BY_SAP_CENTER5) when sapCenter5 set
--       (L490-498).
--     * ORG_LVL_NUM6_COD — LPAD(sapCenter5, 7, '0') when set (L509-510;
--       correction #9).
--     * LCE_GEO_COD_COD — mControl1 || RIGHT(rptBrNo,2) || RIGHT(mControl2,2)
--       (L488, L512; correction #9).
--     * OGL_LN_OFC_COD — mBbtSellOfficer (L514; correction #9).
--     * LN_PUR_COD_COD — constant '3' (L518).
--     * LN_CAT_COD — '001' when dept set (L520-521).
--     * STM_FRQ_COD/NATL/PREF/BASE_CURRENCY — constants (L523-526).
--     * NAS_SRC_COD_COD — constant '130' (L528).
--
--   TODO(valdo-gap): LN_OFC_CUR_COD = l.getMOfficer() (L516; correction #9)
--   has no source column — SHAW_LOAN_MASTER has no M_OFFICER column
--   (verified against SIT ALL_TAB_COLUMNS). The column is OMITTED from this
--   table and must be flagged regression_only in the issue #19 YAML.
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.EXPECTED_32025_TBL AS
      SELECT * FROM (
        SELECT CAST('00040' AS VARCHAR2(5)) AS BK_NUM_ERT,
               CAST(''      AS VARCHAR2(1)) AS REF_NUM_ERT,
               CAST('32025' AS VARCHAR2(5)) AS TRN_COD_ERT,
               CAST('001'   AS VARCHAR2(3)) AS APP_ERT,
               d.ACCT_NUM                   AS LN_NUM_ERT,
               NVL(d.DTE_LAST_RUN, d.BATCH_DATE) AS EFF_DAT_ERT,
               CAST(
                 CASE
                   WHEN (d.M_PAYOFF_TRANS IS NOT NULL
                         AND UPPER(TRIM(d.M_PAYOFF_TRANS)) LIKE '%ST')
                     OR (d.M_TRW_COMMENTS IS NOT NULL
                         AND TRIM(d.M_TRW_COMMENTS) <> ''
                         AND UPPER(TRIM(d.M_TRW_COMMENTS)) = 'AU')
                     THEN 'STL'
                   WHEN d.CHG_OFF_CD IS NOT NULL THEN
                     CASE
                       WHEN TRIM(d.CHG_OFF_CD) IN ('R', 'P', 'N') THEN 'RPO'
                       WHEN TRIM(d.CHG_OFF_CD) IN ('B', 'J') THEN 'B06'
                       WHEN TRIM(d.CHG_OFF_CD) = 'F' THEN 'FCL'
                       WHEN TRIM(d.CHG_OFF_CD) = 'X' THEN 'PRP'
                     END
                 END AS VARCHAR2(3))        AS LGL_STA_COD_COD,
               CAST(
                 CASE
                   WHEN d.CHG_OFF_CD IS NOT NULL
                    AND TRIM(d.CHG_OFF_CD) IN ('X', 'R', 'P', 'N') THEN 1
                   ELSE 0
                 END AS NUMBER(1))          AS RPO_COD_COD,
               CASE
                 WHEN d.CHG_OFF_CD IS NOT NULL
                  AND TRIM(d.CHG_OFF_CD) IN ('X', 'R', 'P', 'N')
                   THEN NVL(d.M_REPO_DTE, d.M_DATE_PAID_OFF)
               END                          AS DAT_LAS_RPO_COD,
               CAST(
                 CASE
                   WHEN d.DUE_DTE_I IS NOT NULL
                     THEN EXTRACT(DAY FROM d.DUE_DTE_I)
                   WHEN d.DUE_DTE_P IS NOT NULL
                     THEN EXTRACT(DAY FROM d.DUE_DTE_P)
                   ELSE 1
                 END AS NUMBER(2))          AS DUE_DAT_DAY_COD,
               CAST('0000001' AS VARCHAR2(7)) AS ORG_LVL_NUM1_COD,
               CAST('0000001' AS VARCHAR2(7)) AS ORG_LVL_NUM2_COD,
               CAST('0000040' AS VARCHAR2(7)) AS ORG_LVL_NUM3_COD,
               CAST(
                 CASE
                   WHEN d.SAP_CENTER5 IS NOT NULL
                    AND org4.ORG_LEVEL4 IS NOT NULL THEN org4.ORG_LEVEL4
                   ELSE '0000102'
                 END AS VARCHAR2(7))        AS ORG_LVL_NUM4_COD,
               CAST('0000001' AS VARCHAR2(7)) AS ORG_LVL_NUM5_COD,
               CASE
                 WHEN d.SAP_CENTER5 IS NOT NULL
                   THEN LPAD(TRIM(d.SAP_CENTER5), 7, '0')
               END                          AS ORG_LVL_NUM6_COD,
               CAST('0000001' AS VARCHAR2(7)) AS ORG_LVL_NUM7_COD,
               CAST('0000000' AS VARCHAR2(7)) AS ORG_LVL_NUM8_COD,
               CAST('0000000' AS VARCHAR2(7)) AS ORG_LVL_NUM9_COD,
               CAST('0000000' AS VARCHAR2(7)) AS ORG_LVL_NUM10_COD,
               CAST('0000000' AS VARCHAR2(7)) AS ORG_LVL_NUM11_COD,
               CAST('0000000' AS VARCHAR2(7)) AS ORG_LVL_NUM12_COD,
               -- LCE_GEO_COD_COD = mControl1 || RIGHT(rptBrNo,2) ||
               -- RIGHT(mControl2,2) (the Java orgLevel4Code, L488/L512).
               CAST(
                 NVL(d.M_CONTROL1, '')
                 || CASE
                      WHEN d.RPT_BR_NO IS NOT NULL
                        THEN SUBSTR(d.RPT_BR_NO, GREATEST(LENGTH(d.RPT_BR_NO) - 1, 1))
                    END
                 || CASE
                      WHEN d.M_CONTROL2 IS NOT NULL
                        THEN SUBSTR(d.M_CONTROL2, GREATEST(LENGTH(d.M_CONTROL2) - 1, 1))
                    END AS VARCHAR2(30))    AS LCE_GEO_COD_COD,
               d.M_BBT_SELL_OFFICER         AS OGL_LN_OFC_COD,
               CAST('3' AS VARCHAR2(1))     AS LN_PUR_COD_COD,
               CASE
                 WHEN d.DEPT IS NOT NULL THEN CAST('001' AS VARCHAR2(3))
               END                          AS LN_CAT_COD,
               CAST('M'   AS VARCHAR2(1))   AS STM_FRQ_COD,
               CAST('USD' AS VARCHAR2(3))   AS NATL_CURRENCY_COD,
               CAST('USD' AS VARCHAR2(3))   AS PREF_CURRENCY_COD,
               CAST('USD' AS VARCHAR2(3))   AS BASE_CURRENCY_COD,
               CAST('130' AS VARCHAR2(3))   AS NAS_SRC_COD_COD
          FROM app_int.V_SHAW_TRANERT_DRIVER d
          LEFT JOIN app_int.LKP_VALDO_SHAW_ORG_LEVEL4_BY_SAP_CENTER5 org4
                 ON org4.SAP_CENTER5 = TRIM(d.SAP_CENTER5)
      ) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/
