-- SQLite-compatible expected-rows query for the DB-to-file reconciliation
-- sample. Targets the SAMPLE_CUSTOMER table seeded by build_sample.py.
--
-- The column names + order must match the mapping JSON exactly so the
-- generic comparator can align the DB result set against the file rows
-- on the CUSTOMER_ID key.

SELECT CUSTOMER_ID,
       NAME,
       EMAIL,
       BALANCE
  FROM SAMPLE_CUSTOMER
 ORDER BY CUSTOMER_ID;
