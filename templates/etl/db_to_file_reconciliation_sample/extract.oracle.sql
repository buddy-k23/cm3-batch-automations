-- Oracle expected-rows query for the DB-to-file reconciliation sample.
-- Targets the SAMPLE_CUSTOMER table; create it locally with the seed
-- block in db_to_file_reconciliation_README.md before running.
--
-- Quoted column aliases preserve the upper-case column names the
-- mapping JSON declares (Oracle would otherwise upper-fold them, which
-- still matches; the quoting is shown for parity with the sqlite file
-- and as a hint for BAs whose file columns carry punctuation -- e.g.
-- "LN-NUM-ERT").

SELECT CUSTOMER_ID AS "CUSTOMER_ID",
       NAME        AS "NAME",
       EMAIL       AS "EMAIL",
       BALANCE     AS "BALANCE"
  FROM SAMPLE_CUSTOMER
 ORDER BY CUSTOMER_ID;
