-- Example: P327 Source Data Query
-- This query demonstrates how to extract trusted source data
-- for validation against generated P327 files

SELECT 
    l.location_code AS "LOCATION-CODE",
    a.account_number AS "ACCT-NUM",
    c.currency_code AS "BASE-CURRENCY",
    a.account_status AS "ACCT-STATUS",
    a.balance AS "CURRENT-BALANCE",
    a.open_date AS "ACCT-OPEN-DATE",
    a.credit_limit AS "CREDIT-LIMIT"
FROM accounts a
JOIN locations l ON a.location_id = l.id
JOIN currencies c ON a.currency_id = c.id
WHERE a.status = 'ACTIVE'
  AND a.balance > 0
  AND a.open_date >= TO_DATE('2024-01-01', 'YYYY-MM-DD')
ORDER BY a.account_number
