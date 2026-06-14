--------------------------------------------------------------------------------
-- SHAW TRANERT L2b SQL-Truth gate: lookup-table bootstrap
--------------------------------------------------------------------------------
-- Creates the seven lookup tables that hold Spring property-file values the
-- TRANERT Java mapper reads via CustomPropertyPlaceholderConfigurer.getProperty(...).
--
-- Idempotency
-- -----------
-- Oracle does not support CREATE TABLE IF NOT EXISTS. Each table is wrapped
-- in a PL/SQL anonymous block that traps ORA-00955 (name is already used by
-- an existing object) and silently continues. Any other DDL error re-raises.
-- The block terminator on column 1 is "/", which the sql_bootstrap.py
-- statement splitter recognises as an Oracle PL/SQL block terminator
-- (see session-5 handover §4.2).
--
-- Population is the responsibility of:
--   * scripts/e2e_lib/extract_tranert_properties.py — reads the live Spring
--     property file and writes the CSVs under
--     config/e2e/sources/SHAW/lookups/ AND regenerates
--     ../10_load/010_load_lookups_from_csv.sql with explicit INSERTs.
--   * 10_load/010_load_lookups_from_csv.sql — the regenerated INSERT bundle,
--     applied AFTER this bootstrap script.
--
-- Table-naming convention and schema placement
-- ---------------------------------------------
-- app_int.LKP_VALDO_SHAW_<purpose>. The LKP_VALDO_ prefix isolates these
-- tables from the source-system tables sharing the same app_int schema,
-- and makes them trivially identifiable as gate-harness artifacts.
--
-- Schema qualification (app_int.) is mandatory per Policy A: the
-- connected Oracle user (uzapp_ad0) is NOT the app_int schema's owner,
-- and unqualified identifiers resolve against the connected user's
-- default schema. Bare CREATE TABLE LKP_VALDO_SHAW_X would silently
-- create the table in uzapp_ad0, not where the harness expects it.
-- The shaw_tranert_smoke classifier refuses bare-identifier DDL/DML.
--
-- Java call-site cross-reference (all line numbers in
-- config/templates/TranertMapper (1).java unless noted):
--   1. tranert.rep_type_ori.<prinSch>                          line 345
--   2. tranert.base.rep_typ_uri.<termsFreq>                    line 357
--   3. loan.master.state.cd.<bk>                               lines 378, 381
--   4. tranert.ledger_cd.dept.<dept>                           line 417, (32040)
--      tranert.ledger_cd.idx3.AAA.dept.<dept>                  line 414
--   5. tranert.act_typ_ui.<accountType> (+ "0"-prefix fallback) (32040)
--   6. tranert.org_level4.sap_center5.<sapCenter5>             line 503
--   7. tranert.32005.cif_act_code_name_relation.<nameRel>      line 121
--
-- Hard rule: no DDL outside this directory; no DML in this file.
--------------------------------------------------------------------------------


--------------------------------------------------------------------------------
-- 1. rep_type_ori by prin_sch
--    Used by TranertMapper.getTranertCus32010 to set REP-TYP-ORI when
--    LoanMasterData.prinSch is non-blank. Default value before this lookup
--    is "001" (TranertMapper line 339).
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
    CREATE TABLE app_int.LKP_VALDO_SHAW_REP_TYPE_ORI_BY_PRIN_SCH (
      PRIN_SCH    VARCHAR2(10) NOT NULL,
      REP_TYP_ORI VARCHAR2(10) NOT NULL,
      CONSTRAINT PK_LKP_REP_TYPE_ORI_BY_PRIN_SCH PRIMARY KEY (PRIN_SCH)
    )
  ';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- 2. rep_typ_uri by terms_freq (BASE-segment override)
--    Used by TranertMapper.getTranertCus32010 to overwrite REP-TYP-ORI when
--    the contact-account row has segment="BASE" and a non-blank termsFreq
--    (CBRS BASE table). Wins over lookup #1.
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
    CREATE TABLE app_int.LKP_VALDO_SHAW_REP_TYP_URI_BY_TERMS_FREQ (
      TERMS_FREQ  VARCHAR2(10) NOT NULL,
      REP_TYP_URI VARCHAR2(10) NOT NULL,
      CONSTRAINT PK_LKP_REP_TYP_URI_BY_TERMS_FREQ PRIMARY KEY (TERMS_FREQ)
    )
  ';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- 3. loan_master state_cd by bk
--    Used by TranertMapper.getTranertCus32010 to resolve ST-COD-ORI from the
--    LoanMasterData.bk code. Falls back to DAOOperations.stateProvinceMap
--    (populated per-batch from the state_province.sql query) when the
--    property-file lookup returns null/empty. See 13-correction list item
--    #13 on issue #17.
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
    CREATE TABLE app_int.LKP_VALDO_SHAW_LOAN_MASTER_STATE_CD_BY_BK (
      BK       VARCHAR2(10) NOT NULL,
      STATE_CD VARCHAR2(10) NOT NULL,
      CONSTRAINT PK_LKP_LOAN_MASTER_STATE_CD_BY_BK PRIMARY KEY (BK)
    )
  ';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- 4. ledger_cd by dept
--    Used by:
--      * TranertMapper.getTranertCus32010 — LN-TYP-ORI (loan-type half,
--        split on ':' and take index [0]); special idx3="AAA" path uses
--        the separate key prefix tranert.ledger_cd.idx3.AAA.dept.<dept>
--        and parses the whole value as an integer (no colon split).
--      * TranertMapper.getTranertCus32040 — ACT-TYP-CBRS (act-type half,
--        split on ':' and take index [1]) under two branches: mMiscCode2='W'
--        with dept set, and the dept-fallback after the act_typ_ui chain.
--
--    The colon-split shape is preserved in two columns so the expected views
--    can join cleanly without re-parsing strings. IDX3_VARIANT distinguishes
--    the standard prefix from the idx3 AAA prefix. Sentinel values:
--      'STD' — standard prefix (tranert.ledger_cd.dept.<dept>)
--      'AAA' — idx3 AAA prefix (tranert.ledger_cd.idx3.AAA.dept.<dept>)
--    Oracle treats empty strings as NULL, so a non-empty sentinel is required
--    to keep IDX3_VARIANT NOT NULL and to support the composite primary key.
--    Composite key (DEPT, IDX3_VARIANT).
--
--    For the AAA variant the property value is a bare integer (no colon),
--    parsed via Integer.valueOf. The extractor writes that integer into
--    LOAN_TYPE and leaves ACT_TYPE null for AAA rows.
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
    CREATE TABLE app_int.LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT (
      DEPT         VARCHAR2(10) NOT NULL,
      IDX3_VARIANT VARCHAR2(5)  NOT NULL,
      LOAN_TYPE    VARCHAR2(20),
      ACT_TYPE     VARCHAR2(20),
      CONSTRAINT PK_LKP_LEDGER_CD_BY_DEPT PRIMARY KEY (DEPT, IDX3_VARIANT),
      CONSTRAINT CK_LKP_LEDGER_CD_VARIANT  CHECK (IDX3_VARIANT IN (''STD'', ''AAA''))
    )
  ';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- 5. act_typ_ui by account_type
--    Used by TranertMapper.getTranertCus32040 to resolve ACT-TYP-CBRS from
--    CBRSSummaryData.accountType. Falls back to a "0"-prefixed key lookup
--    if the unprefixed lookup misses (TranertMapper.getTranertCus32040).
--
--    The "0"-prefix fallback is encoded as separate ACCOUNT_TYPE rows in this
--    table (e.g. both "5" and "05" map to the same ACT_TYP). The expected
--    views perform the unprefixed lookup first, then the prefixed lookup,
--    using COALESCE.
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
    CREATE TABLE app_int.LKP_VALDO_SHAW_ACT_TYP_UI (
      ACCOUNT_TYPE VARCHAR2(10) NOT NULL,
      ACT_TYP      VARCHAR2(10) NOT NULL,
      CONSTRAINT PK_LKP_ACT_TYP_UI PRIMARY KEY (ACCOUNT_TYPE)
    )
  ';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- 6. org_level4 by sap_center5
--    Used by TranertMapper.getTranertCus32025 to resolve ORG-LVL-NUM4-COD
--    when LoanMasterData.sapCenter5 is set. The default value before this
--    lookup is "0000102" (TranertMapper line 506).
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
    CREATE TABLE app_int.LKP_VALDO_SHAW_ORG_LEVEL4_BY_SAP_CENTER5 (
      SAP_CENTER5 VARCHAR2(10) NOT NULL,
      ORG_LEVEL4  VARCHAR2(10) NOT NULL,
      CONSTRAINT PK_LKP_ORG_LEVEL4_BY_SAP_CENTER5 PRIMARY KEY (SAP_CENTER5)
    )
  ';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/


--------------------------------------------------------------------------------
-- 7. cif_act_code by name_relation
--    Used by TranertMapper.getTranertCus32005 to resolve CIF-ACT-COD-CUS
--    from ContactAccountLoan.nameRelationship. The special case
--    primary_ref_flag is false AND cifRefNum == 997 short-circuits to "P"
--    before this lookup is consulted (TranertMapper lines 107-110); the
--    lookup only runs in the else-branch.
--------------------------------------------------------------------------------
BEGIN
  EXECUTE IMMEDIATE '
    CREATE TABLE app_int.LKP_VALDO_SHAW_CIF_ACT_CODE_BY_NAME_RELATION (
      NAME_RELATIONSHIP VARCHAR2(5) NOT NULL,
      CIF_ACT_COD       VARCHAR2(5) NOT NULL,
      CONSTRAINT PK_LKP_CIF_ACT_CODE_BY_NAME_RELATION PRIMARY KEY (NAME_RELATIONSHIP)
    )
  ';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
/
