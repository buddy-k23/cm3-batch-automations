-- ----------------------------------------------------------------
-- SHAW TRANERT L2b SQL-Truth gate: refresh materialized helper tables
-- ----------------------------------------------------------------
-- Shape (B), session 7. The helper tables created (structure-only) by
-- 00_bootstrap/030_expected_tables.sql are populated here. Each table is
-- TRUNCATEd then re-populated via INSERT ... SELECT so the harness can
-- re-run cleanly and always reflects current source data. No DROP is
-- used (the app_int account and the harness whitelist forbid it).
--
-- TRUNCATE is gated by the explicit _TRUNCATE_ALLOWLIST in
-- scripts/e2e_lib/shaw_tranert_smoke.py; every table truncated below must
-- appear in that list (enforced by a drift test).
--
-- Build order matters: V_SHAW_TRANERT_COST_MERGED reads
-- V_SHAW_TRANERT_COST1, so COST1 is refreshed before COST_MERGED.
--
-- Each statement is terminated by ';' on its own line so the bootstrap
-- statement splitter parses every TRUNCATE and INSERT independently.
-- ----------------------------------------------------------------


-- V_SHAW_TRANERT_LOAN_MASTER_BATCH_DATES
TRUNCATE TABLE app_int.V_SHAW_TRANERT_LOAN_MASTER_BATCH_DATES
;
INSERT INTO app_int.V_SHAW_TRANERT_LOAN_MASTER_BATCH_DATES (BATCH_DATE)
  SELECT DISTINCT TRUNC(BATCH_DATE) AS BATCH_DATE
    FROM app_int.SHAW_LOAN_MASTER
   WHERE TRUNC(BATCH_DATE) <= (
           SELECT TRUNC(MAX(BATCH_DATE))
             FROM app_int.BATCH_DATE_LOCATOR
         )
;


-- V_SHAW_TRANERT_DRIVER
TRUNCATE TABLE app_int.V_SHAW_TRANERT_DRIVER
;
INSERT INTO app_int.V_SHAW_TRANERT_DRIVER
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
;


-- V_SHAW_TRANERT_COST1  (refreshed before COST_MERGED)
TRUNCATE TABLE app_int.V_SHAW_TRANERT_COST1
;
INSERT INTO app_int.V_SHAW_TRANERT_COST1 (ACCT_NUM, UNPAID_LCHRGS, FEE_CURR_BAL, FEE_CODE_KEY)
  SELECT c2.ACCT_NUM,
         c1.UNPAID_LCHRGS,
         c1.FEE_CURR_BAL,
         CAST('' AS VARCHAR2(10)) AS FEE_CODE_KEY
    FROM APP_INT.shaw_charge_off c1,
         APP_INT.shaw_loan_master c2
   WHERE c1.ACCOUNT_NUM = c2.ACCT_NUM
     AND c1.UNPAID_LCHRGS IS NOT NULL
     AND c1.FEE_CURR_BAL  IS NOT NULL
;


-- V_SHAW_TRANERT_COST_MERGED  (reads V_SHAW_TRANERT_COST1)
TRUNCATE TABLE app_int.V_SHAW_TRANERT_COST_MERGED
;
INSERT INTO app_int.V_SHAW_TRANERT_COST_MERGED (ACCT_NUM, RCF_DUE_REC)
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
  -- THEN putAll secondary, last-write-wins per account) is UNREACHABLE here:
  -- app_int.SHAW_LOAN_MASTER never carries more than one distinct
  -- TRUNC(BATCH_DATE), so driver_batch_dates.rn=2 never exists and the
  -- secondary cost2 slice is always empty. The dead cost2_secondary CTE and
  -- the cost2_union UNION ALL were removed (issue #22 / §5.1); cost2_union
  -- now reads cost2_primary directly.
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
;


-- V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY
TRUNCATE TABLE app_int.V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY
;
INSERT INTO app_int.V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY
       (ACCOUNT_NUMBER, PORTFOLIO_TYPE, ACCOUNT_TYPE, ACCOUNT_STATUS, PAST_DUE, TERMS_FREQ, DATE_CLOSED)
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
;


-- V_SHAW_TRANERT_LOANS_NAME
TRUNCATE TABLE app_int.V_SHAW_TRANERT_LOANS_NAME
;
INSERT INTO app_int.V_SHAW_TRANERT_LOANS_NAME (KEYED_VALUE)
  SELECT a1.ACCT_NUM || '-' || a2.na_cons_info_ind AS KEYED_VALUE
    FROM app_int.SHAW_LOAN_MASTER a1,
         app_int.SHAW_LOANS_NAME  a2
   WHERE a1.ACCT_NUM   = a2.ACCT_NUM
     AND a1.CHG_OFF_CD = '1'
     AND a2.na_cons_info_ind IS NOT NULL
;


-- V_SHAW_TRANERT_CONTACTS_MERGED  (cross-schema uzapp_ad0.*)
TRUNCATE TABLE app_int.V_SHAW_TRANERT_CONTACTS_MERGED
;
INSERT INTO app_int.V_SHAW_TRANERT_CONTACTS_MERGED
       (CONTACT_ID, NAME_RELATIONSHIP, GOVT_ID_NUM, LEAD_CONTACT_IND, ACCT_NUM,
        CIF_ACT_COD_CUS_RAW, CONS_INFO_IND, CBRS_SEGMENT, CBRS_TERMS_FREQ,
        ECOA_CODE_CUS, SOURCE_DB)
  WITH apps_leg AS (
    -- Explicit CASTs align the UNION ALL with app_int_leg (ORA-01790);
    -- see 00_bootstrap/030_expected_tables.sql for the rationale.
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
;


-- V_SHAW_TRANERT_BK1  (cross-schema uzapp_ad0.cds_acct_bk1)
TRUNCATE TABLE app_int.V_SHAW_TRANERT_BK1
;
INSERT INTO app_int.V_SHAW_TRANERT_BK1
       (ACCT_NUM, BANKRUPTCY_CHAPTER, DISMISSED_DATE, DISCHARGE_DATE, BANKRUPTCY_DATE)
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
;


-- V_SHAW_TRANERT_BK3  (cross-schema uzapp_ad0.cds_acct_bk3)
TRUNCATE TABLE app_int.V_SHAW_TRANERT_BK3
;
INSERT INTO app_int.V_SHAW_TRANERT_BK3
       (ACCT_NUM, BANKRUPTCY_CHAPTER, DISMISSED_DATE, DISCHARGE_DATE, BANKRUPTCY_DATE)
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
;


-- V_SHAW_TRANERT_STATE_PROVINCE  (cross-schema uzapp_ad0.*)
TRUNCATE TABLE app_int.V_SHAW_TRANERT_STATE_PROVINCE
;
INSERT INTO app_int.V_SHAW_TRANERT_STATE_PROVINCE (ACCT_NUM, PORTFOLIO_ID, STATE_PROVINCE)
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
;


-- ----------------------------------------------------------------
-- Per-record-type EXPECTED_*_TBL refresh (commit 5b1 — simple types)
-- ----------------------------------------------------------------
-- The simple per-record-type expected tables created (structure-only) by
-- 00_bootstrap/030_expected_tables.sql are populated here. Each is built on
-- V_SHAW_TRANERT_DRIVER (the single source of the CHG_OFF_CD='1' driver row
-- set), so the detail tables MUST be refreshed AFTER the helper tables above.
-- 32075 reads V_SHAW_TRANERT_COST_MERGED; 32040 reads
-- V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY and the two LKP_VALDO_SHAW_* lookups —
-- all of which are already populated by this point in the load order.
--
-- The SELECT bodies mirror the CTAS bodies in 030_expected_tables.sql exactly
-- (minus the WHERE 1 = 0 structure-only guard). See that file for the Java
-- ground-truth line references behind each column.
-- ----------------------------------------------------------------


-- EXPECTED_32000_TBL  (refresh)
TRUNCATE TABLE app_int.EXPECTED_32000_TBL
;
INSERT INTO app_int.EXPECTED_32000_TBL
       (BK_NUM_ERT, TRN_COD_ERT, APP_ERT, LN_NUM_ERT, EFF_DAT_ERT, LCT_COD_NEW1)
  SELECT CAST('00040' AS VARCHAR2(5)) AS BK_NUM_ERT,
         CAST('32000' AS VARCHAR2(5)) AS TRN_COD_ERT,
         CAST('001'   AS VARCHAR2(3)) AS APP_ERT,
         d.ACCT_NUM                   AS LN_NUM_ERT,
         NVL(d.DTE_LAST_RUN, d.BATCH_DATE) AS EFF_DAT_ERT,
         CAST('100030' AS VARCHAR2(6)) AS LCT_COD_NEW1
    FROM app_int.V_SHAW_TRANERT_DRIVER d
;


-- EXPECTED_32040_TBL  (refresh)
TRUNCATE TABLE app_int.EXPECTED_32040_TBL
;
INSERT INTO app_int.EXPECTED_32040_TBL
       (BK_NUM_ERT, REF_NUM_ERT, TRN_COD_ERT, APP_ERT, EFF_DAT_ERT,
        DAT_DLQ_STR_CBRS, M2F_CMT_COD_CBRS, HGH_AMT_DLQ_CBRS,
        M2F_CMP_CON_COD_CBRS, LN_NUM_ERT, PRE_COF_L1_NUM_CBRS, ACT_TYP_CBRS,
        CUR_PMT_RTG_CBRS, PET_DAT_CBRS, LAS_ACT_STA_CBRS, LAS_DAT_RPT_CBRS,
        LAS_CMT_COD_CBRS)
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
             WHEN TRIM(NVL(d.M_MISC_CODE2, '')) = 'W'
              AND d.DEPT IS NOT NULL
             THEN led_w.ACT_TYPE
             WHEN cbrs.ACCOUNT_TYPE IS NOT NULL
             THEN ui.ACT_TYP
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
    -- Per-account-deduped CBRS join (one value per account, mirroring the
    -- Java Map<acctNum, CBRSSummaryData>); see 030_expected_tables.sql for
    -- why. Keeps 32040 at one_per_driver_row.
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
;


-- EXPECTED_32075_TBL  (refresh; reads V_SHAW_TRANERT_COST_MERGED)
TRUNCATE TABLE app_int.EXPECTED_32075_TBL
;
INSERT INTO app_int.EXPECTED_32075_TBL
       (BK_NUM_ERT, REF_NUM_ERT, TRN_COD_ERT, APP_ERT, LN_NUM_ERT, EFF_DAT_ERT,
        RCF_REF_NUM_REC, RCF_DUE_REC, RCF_ASE_COD_REC, RCF_INT_IND_REC,
        EXP_PYF_IND_REC, RCF_ICR_COD_REC)
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
;


-- EXPECTED_32005_TBL  (refresh; commit 5b2a — per-contact, many_per_driver_row)
-- Composite key (ACCT_NUM, CONTACT_ID). The SELECT body mirrors the CTAS in
-- 030_expected_tables.sql exactly (minus the WHERE 1 = 0 structure guard).
-- See that file for the Java ground-truth line refs and the TODO(valdo-gap)
-- on the deferred CIF_REF_NUM_CUS / 997-'P' short-circuit fields.
TRUNCATE TABLE app_int.EXPECTED_32005_TBL
;
INSERT INTO app_int.EXPECTED_32005_TBL
       (BK_NUM_ERT, REF_NUM_ERT, TRN_COD_ERT, APP_ERT, LN_NUM_ERT, EFF_DAT_ERT,
        CONTACT_ID, CIF_ACT_NUM_CUS, ACCT_NUM, NAME_RELATIONSHIP,
        CIF_ACT_COD_CUS, RESPONSIBLE_PARTY, LEAD_CONTACT_IND, ECOA_CODE_CUS,
        LAST_CHEX_RECORDED_DATE_CUS, ACTION_CODE, LOCATION_CODE,
        CAS_ADDRESS_IND, EXTERNAL_SYSTEM_ID, PREFERRED_CURRENCY,
        CIF_CBR_RPT_IND_CUS, LAST_BUREAU_RECORDED_DATE_CUS,
        CIF_CSM_INF_IND_CUS, DAT_BKY_REC_CUS)
  SELECT CAST('00040' AS VARCHAR2(5)) AS BK_NUM_ERT,
         CAST(''      AS VARCHAR2(1)) AS REF_NUM_ERT,
         CAST('32005' AS VARCHAR2(5)) AS TRN_COD_ERT,
         CAST('001'   AS VARCHAR2(3)) AS APP_ERT,
         d.ACCT_NUM                   AS LN_NUM_ERT,
         NVL(d.DTE_LAST_RUN, d.BATCH_DATE) AS EFF_DAT_ERT,
         CAST(LPAD(c.CONTACT_ID, 15, '0') AS VARCHAR2(15)) AS CONTACT_ID,
         c.CONTACT_ID                 AS CIF_ACT_NUM_CUS,
         c.ACCT_NUM                   AS ACCT_NUM,
         TRIM(c.NAME_RELATIONSHIP)    AS NAME_RELATIONSHIP,
         CAST(cif.CIF_ACT_COD AS VARCHAR2(5)) AS CIF_ACT_COD_CUS,
         CAST('1' AS VARCHAR2(1))     AS RESPONSIBLE_PARTY,
         CAST(
           CASE WHEN UPPER(TRIM(c.NAME_RELATIONSHIP)) = 'A'
                THEN '1' ELSE '0' END AS VARCHAR2(1)) AS LEAD_CONTACT_IND,
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
         CAST(
           CASE WHEN UPPER(TRIM(c.CIF_ACT_COD_CUS_RAW)) = 'N'
                THEN 'N' ELSE 'Y' END AS VARCHAR2(1)) AS CAS_ADDRESS_IND,
         CAST('LS'  AS VARCHAR2(2))   AS EXTERNAL_SYSTEM_ID,
         CAST('USD' AS VARCHAR2(3))   AS PREFERRED_CURRENCY,
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
         CAST(
           CASE
             WHEN TRIM(NVL(d.M_MISC_CODE2, '')) = 'W'
               THEN ln.KEYED_VALUE
             WHEN c.CONS_INFO_IND IS NOT NULL
              AND TRIM(c.CONS_INFO_IND) IS NOT NULL THEN
               CASE
                 WHEN INSTR('I,J,K,L,M,N,O,P,Z', TRIM(c.CONS_INFO_IND)) > 0
                   THEN 'Q'
                 ELSE c.CONS_INFO_IND
               END
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
   WHERE c.NAME_RELATIONSHIP IS NOT NULL
     AND UPPER(TRIM(c.NAME_RELATIONSHIP)) NOT IN ('M', 'N')
;


-- EXPECTED_32010_TBL  (refresh; commit 5b2b — zero_or_one_per_driver_row)
-- The SELECT body mirrors the CTAS in 030_expected_tables.sql exactly (minus
-- the WHERE 1 = 0 structure guard). See that file for Java ground-truth line
-- refs, the ST_COD_ORI leg-A-only decision, and the deduped join rationale.
TRUNCATE TABLE app_int.EXPECTED_32010_TBL
;
INSERT INTO app_int.EXPECTED_32010_TBL
       (BK_NUM_ERT, REF_NUM_ERT, TRN_COD_ERT, APP_ERT, LN_NUM_ERT, EFF_DAT_ERT,
        OGL_CONTRACT_DAT_ORI, OGL_NTE_AMT_ORI, OGL_COF_INT_AMT_ORI,
        CNV_ACT_NUM_ORI, INT_RT_ORI, OGL_TRM_ORI, OGL_MAT_DAT_ORI,
        OGL_CONTRACT_AMT_ORI, OGL_PORTFOLIO_TYP_ORI, COF_CDN_IND_ORI,
        OGL_NTE_DAT_ORI, OGL_INT_RT_ORI, REP_TYP_ORI, HGH_BAL_ORI,
        COF_REA_COD_ORI, ST_COD_ORI, DAT_INT_ACR_TO_ORI, ACT_STA_ORI,
        PCOF_DAT_LAS_PMT_ORI, PCOF_PMT_AMT_LAS_ORI, DAT_LAS_STM_ORI,
        OGL_PMT_AMT_ORI, LN_TYP_ORI, DAT_ACT_CLS_TO_ATY_ORI)
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
    LEFT JOIN app_int.LKP_VALDO_SHAW_REP_TYPE_ORI_BY_PRIN_SCH rep_ps
           ON rep_ps.PRIN_SCH = TRIM(d.PRIN_SCH)
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
    LEFT JOIN app_int.LKP_VALDO_SHAW_LOAN_MASTER_STATE_CD_BY_BK st_bk
           ON st_bk.BK = TRIM(d.BK)
    LEFT JOIN app_int.LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT led_aaa
           ON led_aaa.DEPT = TRIM(d.DEPT)
          AND led_aaa.IDX3_VARIANT = 'AAA'
    LEFT JOIN app_int.LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT led_std
           ON led_std.DEPT = TRIM(d.DEPT)
          AND led_std.IDX3_VARIANT = 'STD'
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
;


-- EXPECTED_32025_TBL  (refresh; commit 5b2b — one_per_driver_row)
-- The SELECT body mirrors the CTAS in 030_expected_tables.sql exactly (minus
-- the WHERE 1 = 0 structure guard). See that file for Java ground-truth line
-- refs and the LN_OFC_CUR_COD TODO(valdo-gap) (no M_OFFICER source column).
TRUNCATE TABLE app_int.EXPECTED_32025_TBL
;
INSERT INTO app_int.EXPECTED_32025_TBL
       (BK_NUM_ERT, REF_NUM_ERT, TRN_COD_ERT, APP_ERT, LN_NUM_ERT, EFF_DAT_ERT,
        LGL_STA_COD_COD, RPO_COD_COD, DAT_LAS_RPO_COD, DUE_DAT_DAY_COD,
        ORG_LVL_NUM1_COD, ORG_LVL_NUM2_COD, ORG_LVL_NUM3_COD, ORG_LVL_NUM4_COD,
        ORG_LVL_NUM5_COD, ORG_LVL_NUM6_COD, ORG_LVL_NUM7_COD, ORG_LVL_NUM8_COD,
        ORG_LVL_NUM9_COD, ORG_LVL_NUM10_COD, ORG_LVL_NUM11_COD,
        ORG_LVL_NUM12_COD, LCE_GEO_COD_COD, OGL_LN_OFC_COD, LN_PUR_COD_COD,
        LN_CAT_COD, STM_FRQ_COD, NATL_CURRENCY_COD, PREF_CURRENCY_COD,
        BASE_CURRENCY_COD, NAS_SRC_COD_COD)
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
;


-- EXPECTED_BATCH_HEADER_TBL  (refresh; ITM_CNT_BRT = sum of materialized
-- detail-table counts — see the 5b1 NOTE in 030_expected_tables.sql.
-- Refreshed LAST so all detail-table counts are current.)
TRUNCATE TABLE app_int.EXPECTED_BATCH_HEADER_TBL
;
INSERT INTO app_int.EXPECTED_BATCH_HEADER_TBL
       (BK_NUM_BRT, APP_BRT, TRN_COD_BRT, BAT_NUM_BRT, INP_SRC_COD_BRT,
        BAT_TYP_BRT, OPR_ID_BRT, ORG_LVL_NUM_1_BRT, ORG_LVL_NUM_2_BRT,
        ORG_LVL_NUM_3_BRT, ORG_LVL_NUM_4_BRT, ORG_LVL_NUM_5_BRT,
        ORG_LVL_NUM_6_BRT, ORG_LVL_NUM_7_BRT, ORG_LVL_NUM_8_BRT,
        ORG_LVL_NUM_9_BRT, ORG_LVL_NUM_10_BRT, ORG_LVL_NUM_11_BRT,
        ORG_LVL_NUM_12_BRT, ITM_CNT_BRT, DR_CR_AMT_BRT)
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
         (
           (SELECT COUNT(*) FROM app_int.EXPECTED_32000_TBL)
         + (SELECT COUNT(*) FROM app_int.EXPECTED_32005_TBL)
         + (SELECT COUNT(*) FROM app_int.EXPECTED_32010_TBL)
         + (SELECT COUNT(*) FROM app_int.EXPECTED_32025_TBL)
         + (SELECT COUNT(*) FROM app_int.EXPECTED_32040_TBL)
         + (SELECT COUNT(*) FROM app_int.EXPECTED_32075_TBL)
         )                                       AS ITM_CNT_BRT,
         CAST('000000000000.00' AS VARCHAR2(22)) AS DR_CR_AMT_BRT
    FROM DUAL
;
