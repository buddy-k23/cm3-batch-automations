-- app_int regression sample setup
-- Run as user with privileges on app_int schema.

-- Drop existing sample tables if they exist
BEGIN EXECUTE IMMEDIATE 'DROP TABLE app_int.src_a_balances PURGE'; EXCEPTION WHEN OTHERS THEN NULL; END;
/
BEGIN EXECUTE IMMEDIATE 'DROP TABLE app_int.src_a_accounts PURGE'; EXCEPTION WHEN OTHERS THEN NULL; END;
/
BEGIN EXECUTE IMMEDIATE 'DROP TABLE app_int.src_b_balances PURGE'; EXCEPTION WHEN OTHERS THEN NULL; END;
/
BEGIN EXECUTE IMMEDIATE 'DROP TABLE app_int.src_b_accounts PURGE'; EXCEPTION WHEN OTHERS THEN NULL; END;
/

CREATE TABLE app_int.src_a_accounts (
  account_id      VARCHAR2(20) PRIMARY KEY,
  status_code     VARCHAR2(10),
  open_date       DATE,
  next_due_date   DATE,
  base_currency   VARCHAR2(3),
  location_code   VARCHAR2(6)
);

CREATE TABLE app_int.src_a_balances (
  account_id      VARCHAR2(20) PRIMARY KEY,
  current_due_amt NUMBER(18,2),
  total_due_amt   NUMBER(18,2)
);

CREATE TABLE app_int.src_b_accounts (
  account_id      VARCHAR2(20) PRIMARY KEY,
  status_code     VARCHAR2(10),
  open_date       DATE,
  next_due_date   DATE,
  base_currency   VARCHAR2(3),
  location_code   VARCHAR2(6)
);

CREATE TABLE app_int.src_b_balances (
  account_id      VARCHAR2(20) PRIMARY KEY,
  current_due_amt NUMBER(18,2),
  total_due_amt   NUMBER(18,2)
);

-- Positive sample rows
INSERT INTO app_int.src_a_accounts VALUES ('10001', 'ACTIVE', DATE '2022-01-01', DATE '2026-03-01', 'USD', 'LS');
INSERT INTO app_int.src_a_balances VALUES ('10001', 150.25, 300.75);

INSERT INTO app_int.src_b_accounts VALUES ('20001', 'ACTIVE', DATE '2023-06-15', DATE '2026-03-05', 'USD', 'NY');
INSERT INTO app_int.src_b_balances VALUES ('20001', 10.00, 20.00);

-- Negative sample rows (for rule failures)
INSERT INTO app_int.src_a_accounts VALUES ('10002', 'BADSTATUS', DATE '2020-12-31', DATE '2020-01-01', 'XXX', NULL);
INSERT INTO app_int.src_a_balances VALUES ('10002', 500.00, 100.00);

INSERT INTO app_int.src_b_accounts VALUES ('20002', 'ACTIVE', DATE '2024-01-01', DATE '2024-01-10', 'USD', '');
INSERT INTO app_int.src_b_balances VALUES ('20002', 900.00, 100.00);

COMMIT;
