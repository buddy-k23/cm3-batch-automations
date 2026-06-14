SELECT
  lpad(a.account_id, 16, '0') AS "ACCT-NUM",
  a.status_code AS "ACCT-STAT-CODE-1",
  TO_CHAR(a.open_date, 'YYYYMMDD') AS "ACCT-OPEN-DATE",
  TO_CHAR(a.next_due_date, 'YYYYMMDD') AS "NEXT-DUE-DATE",
  TO_CHAR(NVL(b.current_due_amt, 0), 'FM000000000000000000') AS "CURRENT-DUE-AMT",
  TO_CHAR(NVL(b.total_due_amt, 0), 'FM000000000000000000') AS "TOTAL-DUE-AMT",
  a.base_currency AS "BASE-CURRENCY",
  a.location_code AS "LOCATION-CODE"
FROM app_int.src_b_accounts a
LEFT JOIN app_int.src_b_balances b
  ON a.account_id = b.account_id
ORDER BY a.account_id
