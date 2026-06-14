-- Standalone parameterised cost2 ad-hoc query.
--
-- Mirrors: shaw.tranert.37025.fee.cost2.sql
--
-- Returns: (ACCT_NUM, UNPAID_LCHRGS, FEE_CURR_BAL, FEE_CODE_KEY)
--          historical-fee rows from app_int.SHAW_LOAN_MASTER_HISTORY +
--          APP_INT.SHAW_FEE_MASTER_HISTORY filtered to the 28 fee codes
--          the Java DAOOperations.getTranert() loop consumes, restricted
--          to driver-scoped accounts on the requested batch date and
--          the requested rank offset over SHAW_LOAN_MASTER_HISTORY's
--          distinct batch dates DESC.
--
-- Binds:
--   :batch_date   The driver-batch TRUNC(BATCH_DATE) of SHAW_LOAN_MASTER.
--                 One of the values V_SHAW_TRANERT_LOAN_MASTER_BATCH_DATES
--                 returns (see ``20_query/loan_master_batch_dates.sql``).
--   :rank_offset  The dense_rank() offset over SHAW_LOAN_MASTER_HISTORY's
--                 distinct batch dates DESC. Java passes 2 first (the
--                 prior batch) and falls back to 1 (most recent) only
--                 when getShawLoanMasterBatchDates() returns exactly two
--                 dates.
--
-- Note: This is NOT a CREATE VIEW. Oracle refuses ``CREATE VIEW`` with
-- bind references (ORA-01027). The cost-merge logic lives inside
-- V_SHAW_TRANERT_COST_MERGED (materialized as a table in 00_bootstrap/030_expected_tables.sql)
-- which inlines a non-parameterised equivalent. This file is published
-- for ad-hoc / DBA execution; the reconciliation engine consumes
-- V_SHAW_TRANERT_COST_MERGED.
--
-- Example (python-oracledb):
--     cursor.execute(
--         open("20_query/cost2.sql").read(),
--         {"batch_date": batch_date, "rank_offset": 2},
--     )

SELECT a1.ACCT_NUM,
       a1.UNPAID_LCHRGS,
       a3.FEE_CURR_BAL,
       a3.FEE_CODE_KEY
  FROM app_int.SHAW_LOAN_MASTER_HISTORY a1
  LEFT JOIN APP_INT.SHAW_FEE_MASTER_HISTORY a3
         ON a1.ACCT_NUM   = a3.ACCT_NUM
        AND a1.BATCH_DATE = a3.BATCH_DATE
        AND a3.FEE_CODE_KEY IN (
              'BKAP', 'BKRP', 'CNRF', 'DAUC', 'DANL',
              'DFLD', 'DHAZ', 'DKEY', 'DMSC', 'DNDP',
              'DNSF', 'DOTH', 'DPPY', 'DREP', 'DSTO',
              'DTAX', 'FAPP', 'FLEG', 'RLEG', 'RPRD',
              'RPRS', 'SAUC', 'SFMS', 'SFNS', 'SFOC',
              'SKEY', 'SPPY', 'SREP', 'SSTO'
            )
 CROSS JOIN app_int.SHAW_LOAN_MASTER a2
 WHERE a1.ACCT_NUM = a2.ACCT_NUM
   AND a2.CHG_OFF_CD = '1'
   AND TRUNC(a2.M_DATE_PAID_OFF) = TRUNC(a2.BATCH_DATE)
   AND TRUNC(a2.BATCH_DATE) = :batch_date
   AND TRUNC(a1.BATCH_DATE) = (
         SELECT TRUNC(BATCH_DATE)
           FROM (SELECT DISTINCT BATCH_DATE,
                        DENSE_RANK() OVER (ORDER BY BATCH_DATE DESC) r
                   FROM app_int.SHAW_LOAN_MASTER_HISTORY)
          WHERE r = :rank_offset
       )
