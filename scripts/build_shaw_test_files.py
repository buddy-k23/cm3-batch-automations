"""Generate the manual SHAW TRANERT test fixtures and matching SQL seed.

This script materializes the three fixed-width test files referenced by
``tests/manual/TEST_PLAN.md``:

    tests/manual/fixtures/tranert_shaw_test_valid.txt
    tests/manual/fixtures/tranert_shaw_test_structural_failures.txt
    tests/manual/fixtures/tranert_shaw_test_clean_no_violations.txt

…and, atomically, the two SQL seed files that mirror the VALID fixture
1:1 for L2b reconciliation:

    tests/manual/sql/shaw_setup.sql         (Oracle)
    tests/manual/sql/shaw_setup_sqlite.sql  (SQLite — local/Oracle-free)

Each EXPECTED_*_TBL gets exactly as many rows as the matching record
type contributes to ``tranert_shaw_test_valid.txt`` (5 NEW1, 4 CUS,
3 ORI, 2 COD, 2 CBRS, 2 REC, 1 BATCH_HEADER), with field values copied
verbatim from the same ``detail_overrides()`` / ``header_overrides()``
that build the fixture rows. That guarantees the L2b comparator sees
zero violations when run against the VALID fixture.

All three fixture files share the TRANERT umbrella's GLOBAL record
width — the maximum ``position + length - 1`` across every record-type
mapping referenced by ``config/mappings/SHAW_TRANERT.yaml``. ORI is the
widest (636 chars), so every line in every fixture is padded right to
636.

The generator reads the real mapping JSONs at runtime; it is not pinned
to a snapshot of field lists. Re-run it whenever the mappings change and
both the fixtures AND the seed will track.

Usage
-----
    .venv311/bin/python scripts/build_shaw_test_files.py

The script is idempotent (overwrites all five files) and prints a short
summary on stdout. Exits non-zero if any line ends up the wrong width.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
MAPPINGS_DIR = REPO_ROOT / "config" / "mappings"
FIXTURE_DIR = REPO_ROOT / "tests" / "manual" / "fixtures"
SQL_DIR = REPO_ROOT / "tests" / "manual" / "sql"

# Record-type → mapping JSON file (mirrors SHAW_TRANERT.yaml).
RECORD_TYPE_MAPPINGS: Dict[str, str] = {
    "batch_header": "SHAW_TRANERT_BATCH_HEADER_mapping.json",
    "new1": "SHAW_TRANERT_NEW1_mapping.json",
    "cus": "SHAW_TRANERT_CUS_mapping.json",
    "ori": "SHAW_TRANERT_ORI_mapping.json",
    "cod": "SHAW_TRANERT_COD_mapping.json",
    "cbrs": "SHAW_TRANERT_CBRS_mapping.json",
    "rec": "SHAW_TRANERT_REC_mapping.json",
}

# Discriminator codes the umbrella expects.
TRN_COD_BY_TYPE: Dict[str, str] = {
    "new1": "32000",  # Could also be 32001; we use 32000 (NAS) for valid rows.
    "cus": "32005",
    "ori": "32010",
    "cod": "32025",
    "cbrs": "32040",
    "rec": "32075",
}

# Field name → static default (string, blank-padded).
# Numerics get zero-padded; dates get a fixed MMDDYYYY value; everything
# else falls back to the generic helpers below.
DATE_DEFAULT = "06012026"  # MMDDYYYY for 2026-06-01 — placeholder for date fields.

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_mapping(record_type: str) -> dict:
    """Load the JSON mapping for ``record_type``."""
    fn = MAPPINGS_DIR / RECORD_TYPE_MAPPINGS[record_type]
    if not fn.exists():
        raise FileNotFoundError(f"Missing mapping JSON: {fn}")
    with fn.open() as fh:
        return json.load(fh)


def expected_length(mapping: dict) -> int:
    """Return the right-most byte position used by the mapping (1-indexed)."""
    return max(f["position"] + f["length"] - 1 for f in mapping["fields"])


def global_record_width() -> int:
    """Compute the umbrella's global record width across all 7 record types."""
    return max(expected_length(load_mapping(rt)) for rt in RECORD_TYPE_MAPPINGS)


def field_value(
    field: dict,
    overrides: Dict[str, str],
    *,
    row_kind: str,
    seq: int,
) -> str:
    """Return a deterministic, type-correct value for ``field``.

    Args:
        field: A single mapping ``fields`` entry.
        overrides: Caller-supplied {field_name: literal_value}. Always wins.
        row_kind: One of the keys of ``RECORD_TYPE_MAPPINGS``.
        seq: 1-indexed row-of-its-kind counter, used to vary key columns.

    Returns:
        A string of exactly ``field['length']`` characters.
    """
    length = field["length"]
    name = field["name"]
    dtype = field["data_type"]

    if name in overrides:
        raw = overrides[name]
    elif dtype == "date":
        raw = DATE_DEFAULT  # 8 chars; date-field lengths are typically 8 or 10.
    elif dtype in ("decimal", "int", "num", "numeric"):
        raw = str(seq)
    elif dtype == "boolean":
        raw = "0"
    else:
        # String fallback — pad with blanks, no FILLER weirdness.
        raw = ""

    # Width fitting. Numerics zero-pad left; everything else blank-pads right.
    if dtype in ("decimal", "int", "num", "numeric", "boolean"):
        return raw[-length:].rjust(length, "0") if raw else "0" * length
    # Strings (and dates rendered as strings) are blank-padded right.
    return raw.ljust(length)[:length]


def build_record(
    record_type: str,
    width: int,
    *,
    overrides: Dict[str, str] | None = None,
    seq: int = 1,
) -> str:
    """Build a single fixed-width line for ``record_type``, padded to ``width``."""
    mapping = load_mapping(record_type)
    overrides = overrides or {}

    # Assemble byte-by-byte using the mapping spec, so gaps (FILLERs the
    # workbook didn't carve out) come through as blanks.
    line_chars: List[str] = [" "] * width
    for field in mapping["fields"]:
        start = field["position"] - 1
        end = start + field["length"]
        value = field_value(field, overrides, row_kind=record_type, seq=seq)
        line_chars[start:end] = list(value)
    return "".join(line_chars)


def header_overrides(item_count: int) -> Dict[str, str]:
    """BATCH_HEADER overrides anchoring the ITM-CNT-BRT assertion."""
    return {
        "BK-NUM-BRT": "00001",
        "APP-BRT": "200",
        "EFF-DAT-BRT": "06/01/2026",        # MM/DD/CCYY (length 10)
        "TRN-COD-BRT": "BATCH",
        "BAT-NUM-BRT": "0000001",
        "INP-SRC-COD-BRT": "001",
        "BAT-TYP-BRT": "32",                # NAS/EAS batches per spec
        "OPR-ID-BRT": "VALDOTST",
        "ITM-CNT-BRT": str(item_count).zfill(9),
        "DR-CR-AMT-BRT": "0.00".rjust(22),  # NAS/EAS batches use 0.00 per mapping
    }


def detail_overrides(record_type: str, seq: int) -> Dict[str, str]:
    """Shared key columns for detail records so reconciliation has stable keys."""
    return {
        "BK-NUM-ERT": "00001",
        "APP-ERT": "200",
        "LN-NUM-ERT": f"LN{seq:016d}",
        "EFF-DAT-ERT": "06012026",                # MMDDYYYY (length 8/10)
        "TRN-COD-ERT": TRN_COD_BY_TYPE[record_type],
    }


# ---------------------------------------------------------------------------
# File builders
# ---------------------------------------------------------------------------


def build_valid_file(width: int) -> List[str]:
    """Build the ~30-line VALID file: 1 BATCH_HEADER + 18 detail rows."""
    rows: List[str] = []
    rows.append(build_record("batch_header", width, overrides=header_overrides(18)))

    counts = [("new1", 5), ("cus", 4), ("ori", 3), ("cod", 2), ("cbrs", 2), ("rec", 2)]
    for rtype, n in counts:
        for i in range(1, n + 1):
            rows.append(build_record(rtype, width, overrides=detail_overrides(rtype, i), seq=i))
    return rows


def build_clean_file(width: int) -> List[str]:
    """Minimal clean baseline — 1 BATCH_HEADER + 1 NEW1 detail row."""
    rows: List[str] = []
    rows.append(build_record("batch_header", width, overrides=header_overrides(1)))
    rows.append(build_record("new1", width, overrides=detail_overrides("new1", 1), seq=1))
    return rows


def build_failure_file(width: int) -> List[str]:
    """Build the structural-failure fixture.

    Defects (deliberate):
      Line 1 — BATCH_HEADER with ITM-CNT-BRT=0099 but only 4 detail rows
               (header_trailer_count assertion fires).
      Line 2 — NEW1 line truncated to width-50 chars (length mismatch).
      Line 3 — NEW1 with non-numeric in BK-NUM-ERT (numeric-type violation).
      Line 4 — Detail line with unknown TRN-COD-ERT=99999 (unknown record type;
               default_action: error fires).
      Line 5 — Clean NEW1 row so the file isn't trivially malformed.
    """
    rows: List[str] = []
    rows.append(build_record("batch_header", width, overrides={**header_overrides(0), "ITM-CNT-BRT": "000000099"}))

    # Truncated NEW1 — chop off the last 50 chars after building it correctly.
    truncated = build_record("new1", width, overrides=detail_overrides("new1", 1), seq=1)
    rows.append(truncated[: width - 50])

    # Numeric type violation: BK-NUM-ERT (decimal, len 5) → "ABCDE".
    bad_numeric = build_record(
        "new1",
        width,
        overrides={**detail_overrides("new1", 2), "BK-NUM-ERT": "ABCDE"},
        seq=2,
    )
    rows.append(bad_numeric)

    # Unknown discriminator: build a NEW1 shape (so widths line up) but force
    # TRN-COD-ERT to 99999. The multi-record dispatcher should reject this row.
    unknown_disc = build_record(
        "new1",
        width,
        overrides={**detail_overrides("new1", 3), "TRN-COD-ERT": "99999"},
        seq=3,
    )
    rows.append(unknown_disc)

    # One clean NEW1 row so the file has at least one valid detail row.
    rows.append(build_record("new1", width, overrides=detail_overrides("new1", 4), seq=4))
    return rows


# ---------------------------------------------------------------------------
# SQL seed emitters
#
# Both Oracle and SQLite seed files are produced from the SAME row plan
# the fixture builder uses (``build_valid_file``-shaped: 1 BATCH_HEADER +
# 5 NEW1 + 4 CUS + 3 ORI + 2 COD + 2 CBRS + 2 REC). The Oracle file
# preserves the existing DDL contract from the prior commit (NUMBER /
# VARCHAR2 / DATE / TIMESTAMP); the SQLite file substitutes TEXT /
# INTEGER / TEXT with CURRENT_TIMESTAMP defaults.
# ---------------------------------------------------------------------------


# Row plan: identical to build_valid_file. (record_type, total_seq).
VALID_ROW_PLAN: List[Tuple[str, int]] = [
    ("new1", 5),
    ("cus", 4),
    ("ori", 3),
    ("cod", 2),
    ("cbrs", 2),
    ("rec", 2),
]

# EXPECTED_*_TBL key columns shared by every detail record-type row.
# Values are written into the matching SQL INSERT verbatim so they line
# up byte-for-byte with the values in the fixture file.
RECORD_TYPE_TO_TABLE: Dict[str, str] = {
    "new1": "EXPECTED_NEW1_TBL",
    "cus": "EXPECTED_CUS_TBL",
    "ori": "EXPECTED_ORI_TBL",
    "cod": "EXPECTED_COD_TBL",
    "cbrs": "EXPECTED_CBRS_TBL",
    "rec": "EXPECTED_REC_TBL",
}


def _detail_insert_oracle(record_type: str, seq: int) -> str:
    """Emit one Oracle INSERT for the given detail record-type/seq.

    Mirrors :func:`detail_overrides` exactly:
      BK_NUM_ERT = 1, APP_ERT = 200, LN_NUM_ERT = 'LN{seq:016d}',
      EFF_DAT_ERT = DATE '2026-06-01', TRN_COD_ERT = per-record-type code.
    """
    table = RECORD_TYPE_TO_TABLE[record_type]
    trn_cod = TRN_COD_BY_TYPE[record_type]
    return (
        f"INSERT INTO {table} (BK_NUM_ERT, APP_ERT, LN_NUM_ERT, EFF_DAT_ERT, TRN_COD_ERT) "
        f"VALUES (1, 200, 'LN{seq:016d}', DATE '2026-06-01', {trn_cod});"
    )


def _detail_insert_sqlite(record_type: str, seq: int) -> str:
    """SQLite variant — DATE literal becomes an ISO-8601 text value."""
    table = RECORD_TYPE_TO_TABLE[record_type]
    trn_cod = TRN_COD_BY_TYPE[record_type]
    return (
        f"INSERT INTO {table} (BK_NUM_ERT, APP_ERT, LN_NUM_ERT, EFF_DAT_ERT, TRN_COD_ERT) "
        f"VALUES (1, 200, 'LN{seq:016d}', '2026-06-01', {trn_cod});"
    )


# --- DDL blocks (kept inline so the generator is self-contained) -----------

_ORACLE_DDL_HEADER = """\
-- =============================================================================
-- shaw_setup.sql -- SHAW manual-test schema bootstrap for Valdo Engine v3.
--
-- GENERATED FILE. Source of truth: scripts/build_shaw_test_files.py.
-- Edit the generator, re-run it, and commit both fixture + SQL together.
--
-- Purpose
--   1. Six SHAW_* staging tables (placeholder shape) seeded with synthetic
--      rows so file_to_staging gate smoke checks have data to read.
--   2. Seven EXPECTED_*_TBL reconciliation tables (one per TRANERT record
--      type plus BATCH_HEADER) seeded with one row per detail row of
--      tests/manual/fixtures/tranert_shaw_test_valid.txt -- the L2b
--      comparator should report ZERO violations against that fixture.
--
-- Apply with:
--   sqlplus app_int/<pwd>@localhost:1521/FREEPDB1 @tests/manual/sql/shaw_setup.sql
-- ...or use the cross-backend runner:
--   python tests/manual/seed_db.py --backend oracle
-- =============================================================================

SET ECHO ON
SET FEEDBACK ON
WHENEVER SQLERROR CONTINUE

-- -----------------------------------------------------------------------------
-- 1) SHAW_* staging tables (placeholder shape -- see TEST_PLAN.md Section 0).
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
-- One table per TRANERT record type; columns derived from each mapping
-- JSON's `target_name` / `data_type` / `length`. Identifiers starting
-- with a digit (e.g. 10_98_RPT_IND_COD, 24_CYC_DLQ_*_CBRS) are
-- double-quoted -- required by Oracle.
-- -----------------------------------------------------------------------------

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
"""


_ORACLE_DDL_FOOTER = """\

-- -----------------------------------------------------------------------------
-- Optional teardown -- uncomment as a block to reset between runs.
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
"""


_SQLITE_DDL_HEADER = """\
-- =============================================================================
-- shaw_setup_sqlite.sql -- Oracle-free SHAW manual-test schema for Valdo v3.
--
-- GENERATED FILE. Source of truth: scripts/build_shaw_test_files.py.
--
-- Apply with:
--   sqlite3 tests/manual/valdo_test.db < tests/manual/sql/shaw_setup_sqlite.sql
-- ...or, preferred (handles --drop-first, prints summary):
--   python tests/manual/seed_db.py --backend sqlite
--
-- Type substitutions vs. Oracle:
--   VARCHAR2(N)                -> TEXT          (length ignored in SQLite)
--   NUMBER(N)                  -> INTEGER       (or REAL where needed)
--   DATE                       -> TEXT          (ISO-8601 YYYY-MM-DD)
--   TIMESTAMP DEFAULT SYSTIMESTAMP -> TEXT DEFAULT CURRENT_TIMESTAMP
-- Identifiers that begin with a digit are wrapped in double quotes,
-- same as Oracle. SQLite accepts the same syntax.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1) SHAW_* staging tables (placeholder shape).
-- -----------------------------------------------------------------------------

CREATE TABLE SHAW_COLLATERAL (
    BATCH_DATE  TEXT,
    RECORD_KEY  TEXT,
    LINE_NO     INTEGER,
    RAW_LINE    TEXT,
    LOAD_TS     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE SHAW_FEE_MASTER (
    BATCH_DATE  TEXT,
    RECORD_KEY  TEXT,
    LINE_NO     INTEGER,
    RAW_LINE    TEXT,
    LOAD_TS     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE SHAW_LOAN_MASTER (
    BATCH_DATE  TEXT,
    RECORD_KEY  TEXT,
    LINE_NO     INTEGER,
    RAW_LINE    TEXT,
    LOAD_TS     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE SHAW_LOANS_NAME (
    BATCH_DATE  TEXT,
    RECORD_KEY  TEXT,
    LINE_NO     INTEGER,
    RAW_LINE    TEXT,
    LOAD_TS     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE SHAW_TRANSACTIONS (
    BATCH_DATE  TEXT,
    RECORD_KEY  TEXT,
    LINE_NO     INTEGER,
    RAW_LINE    TEXT,
    LOAD_TS     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE SHAW_TRANS_MASTER (
    BATCH_DATE  TEXT,
    RECORD_KEY  TEXT,
    LINE_NO     INTEGER,
    RAW_LINE    TEXT,
    LOAD_TS     TEXT DEFAULT CURRENT_TIMESTAMP
);

-- -----------------------------------------------------------------------------
-- 2) EXPECTED_*_TBL reconciliation tables.
-- -----------------------------------------------------------------------------

CREATE TABLE EXPECTED_BATCH_HEADER_TBL (
    BK_NUM_BRT          INTEGER,
    APP_BRT             INTEGER,
    EFF_DAT_BRT         TEXT,
    TRN_COD_BRT         TEXT,
    BAT_NUM_BRT         INTEGER,
    INP_SRC_COD_BRT     INTEGER,
    BAT_TYP_BRT         INTEGER,
    OPR_ID_BRT          TEXT,
    ORG_LVL_NUM_1_BRT   INTEGER,
    ORG_LVL_NUM_2_BRT   INTEGER,
    ORG_LVL_NUM_3_BRT   INTEGER,
    ORG_LVL_NUM_4_BRT   INTEGER,
    ORG_LVL_NUM_5_BRT   INTEGER,
    ORG_LVL_NUM_6_BRT   INTEGER,
    ORG_LVL_NUM_7_BRT   INTEGER,
    ORG_LVL_NUM_8_BRT   INTEGER,
    ORG_LVL_NUM_9_BRT   INTEGER,
    ORG_LVL_NUM_10_BRT  INTEGER,
    ORG_LVL_NUM_11_BRT  INTEGER,
    ORG_LVL_NUM_12_BRT  INTEGER,
    ITM_CNT_BRT         INTEGER,
    DR_CR_AMT_BRT       REAL
);

CREATE TABLE EXPECTED_NEW1_TBL (
    BK_NUM_ERT          INTEGER,
    APP_ERT             INTEGER,
    LN_NUM_ERT          TEXT,
    REF_NUM_ERT         TEXT,
    FILLER              TEXT,
    EFF_DAT_ERT         TEXT,
    TRN_COD_ERT         INTEGER,
    BAT_ITM_NUM_ERT     INTEGER,
    INP_SRC_COD_ERT     INTEGER,
    TRN_CNT_ERT         INTEGER,
    TRN_MOR_DTA_FLG_ERT TEXT,
    SEL_ID_NEW          TEXT,
    LCT_COD_NEW1        TEXT,
    USR_ID_NEW1         TEXT
);

CREATE TABLE EXPECTED_CUS_TBL (
    BK_NUM_ERT                    INTEGER,
    APP_ERT                       INTEGER,
    LN_NUM_ERT                    TEXT,
    REF_NUM_ERT                   TEXT,
    FILLER                        TEXT,
    EFF_DAT_ERT                   TEXT,
    TRN_COD_ERT                   INTEGER,
    BAT_ITM_NUM_ERT               INTEGER,
    INP_SRC_COD_ERT               INTEGER,
    TRN_CNT_ERT                   INTEGER,
    TRN_MOR_DTA_FLG_ERT           TEXT,
    CIF_ACT_NUM_CUS               TEXT,
    CIF_ACT_COD_CUS               TEXT,
    CIF_CBR_RPT_IND_CUS           TEXT,
    CIF_CSM_INF_IND_CUS           TEXT,
    DAT_BKY_REC_CUS               TEXT,
    CIF_ACT_COD_DES_CUS           TEXT,
    CIF_REF_NUM_CUS               TEXT,
    LAS_CSM_INF_IND_CUS           TEXT,
    ECOA_CODE_CUS                 TEXT,
    LAST_ECOA_CODE_CUS            TEXT,
    LAST_BUREAU_RECORDED_DATE_CUS TEXT,
    FINAL_REPORT_INDICATOR_CUS    TEXT,
    LAST_REPORTED_SEG_TYPE_CUS    TEXT,
    LAST_CHEX_RECORDED_DATE_CUS   TEXT,
    ACTION_CODE                   TEXT,
    CONTACT_ID                    TEXT,
    LOCATION_CODE                 TEXT,
    ACCT_NUM                      TEXT,
    NAME_RELATIONSHIP             TEXT,
    LEAD_CONTACT_IND              INTEGER,
    RESPONSIBLE_PARTY             INTEGER,
    CAS_ADDRESS_IND               TEXT,
    EXTERNAL_SYSTEM_ID            TEXT,
    PREFERRED_CURRENCY            TEXT
);

CREATE TABLE EXPECTED_ORI_TBL (
    BK_NUM_ERT             INTEGER,
    APP_ERT                INTEGER,
    LN_NUM_ERT             TEXT,
    REF_NUM_ERT            TEXT,
    FILLER                 TEXT,
    EFF_DAT_ERT            TEXT,
    TRN_COD_ERT            INTEGER,
    BAT_ITM_NUM_ERT        INTEGER,
    INP_SRC_COD_ERT        INTEGER,
    TRN_CNT_ERT            INTEGER,
    TRN_MOR_DTA_FLG_ERT    TEXT,
    OGL_CONTRACT_DAT_ORI   TEXT,
    OGL_TRM_ORI            INTEGER,
    OGL_MAT_DAT_ORI        TEXT,
    OGL_CONTRACT_AMT_ORI   REAL,
    OGL_PORTFOLIO_TYP_ORI  TEXT,
    LN_TYP_ORI             INTEGER,
    COF_CDN_IND_ORI        INTEGER,
    OGL_NTE_DAT_ORI        TEXT,
    OGL_NTE_AMT_ORI        REAL,
    OGL_COF_INT_AMT_ORI    REAL,
    INT_RT_ORI             REAL,
    OGL_INT_RT_ORI         REAL,
    REP_TYP_ORI            INTEGER,
    HGH_BAL_ORI            REAL,
    AMT_PAS_DUE_30_ORI     REAL,
    AMT_PAS_DUE_60_ORI     REAL,
    AMT_PAS_DUE_90_ORI     REAL,
    AMT_PAS_DUE_120_ORI    REAL,
    OGL_NUM_PMT_MTD_ORI    INTEGER,
    OGL_NUM_PMT_YTD_ORI    INTEGER,
    OGL_NUM_PMT_LTD_ORI    INTEGER,
    OGL_PMT_AMT_MTD_ORI    REAL,
    OGL_PMT_AMT_YTD_ORI    REAL,
    OGL_PMT_AMT_LTD_ORI    REAL,
    FIL_FLD_ORI            TEXT,
    COF_REA_COD_ORI        TEXT,
    ST_COD_ORI             TEXT,
    DAT_INT_ACR_TO_ORI     TEXT,
    ACT_STA_ORI            INTEGER,
    MTG_AGC_ID_ORI         TEXT,
    PCOF_DAT_LAS_PMT_ORI   TEXT,
    MTG_ID_NUM_ORI         TEXT,
    DAT_LAS_STM_ORI        TEXT,
    PCOF_PMT_AMT_LAS_ORI   REAL,
    CNV_ACT_NUM_ORI        TEXT,
    OGL_PMT_AMT_ORI        REAL,
    DAT_ACT_CLS_TO_ATY_ORI TEXT
);

CREATE TABLE EXPECTED_COD_TBL (
    BK_NUM_ERT               INTEGER,
    APP_ERT                  INTEGER,
    LN_NUM_ERT               TEXT,
    REF_NUM_ERT              TEXT,
    FILLER                   TEXT,
    EFF_DAT_ERT              TEXT,
    TRN_COD_ERT              INTEGER,
    BAT_ITM_NUM_ERT          INTEGER,
    INP_SRC_COD_ERT          INTEGER,
    TRN_CNT_ERT              INTEGER,
    TRN_MOR_DTA_FLG_ERT      TEXT,
    STP_ACR_ATY_COD_COD      INTEGER,
    DAT_PLC_IN_STP_COD       TEXT,
    DAT_STP_ACR_RMD_COD      TEXT,
    INT_ACR_WHI_STP_COD      REAL,
    LGL_STA_COD_COD          TEXT,
    DAT_LAS_RPO_COD          TEXT,
    RPO_COD_COD              INTEGER,
    DUE_DAT_DAY_COD          INTEGER,
    DAT_RMD_RPO_COD          TEXT,
    FCL_STA_IND_COD          TEXT,
    ORG_LVL_NUM_1_COD        INTEGER,
    ORG_LVL_NUM_2_COD        INTEGER,
    ORG_LVL_NUM_3_COD        INTEGER,
    ORG_LVL_NUM_4_COD        INTEGER,
    ORG_LVL_NUM_5_COD        INTEGER,
    ORG_LVL_NUM_6_COD        INTEGER,
    ORG_LVL_NUM_7_COD        INTEGER,
    ORG_LVL_NUM_8_COD        INTEGER,
    ORG_LVL_NUM_9_COD        INTEGER,
    ORG_LVL_NUM_10_COD       INTEGER,
    ORG_LVL_NUM_11_COD       INTEGER,
    ORG_LVL_NUM_12_COD       INTEGER,
    "10_98_RPT_IND_COD"      TEXT,
    LCE_GEO_COD_COD          TEXT,
    CR_RT_COD                TEXT,
    CAL_RPT_COD_COD          TEXT,
    OGL_LN_POO_NUM_COD       INTEGER,
    OGL_LN_OFC_COD           TEXT,
    LN_OFC_CUR_COD           TEXT,
    OGL_BR_NUM_COD           INTEGER,
    LN_PUR_COD_COD           TEXT,
    LN_CAT_COD               INTEGER,
    DLR_NUM_COD              INTEGER,
    FCL_EFF_DAT_COD          TEXT,
    STM_FRQ_COD              TEXT,
    NATL_CURRENCY_COD        TEXT,
    PREF_CURRENCY_COD        TEXT,
    BASE_CURRENCY_COD        TEXT,
    PRT_COF_BK_NUM_COD       INTEGER,
    PRT_COF_APP_COD          INTEGER,
    PRT_COF_LN_NUM_COD       TEXT,
    NAS_SRC_COD_COD          INTEGER,
    HMDA_ULI_CODE            TEXT,
    IRS_PROP_SECURE_TYPE_COD TEXT,
    IRS_PROP_ADDR_DESC_COD   TEXT,
    IRS_NUM_OF_MORT_COD      INTEGER
);

CREATE TABLE EXPECTED_CBRS_TBL (
    BK_NUM_ERT             INTEGER,
    APP_ERT                INTEGER,
    LN_NUM_ERT             TEXT,
    REF_NUM_ERT            TEXT,
    FILLER                 TEXT,
    EFF_DAT_ERT            TEXT,
    TRN_COD_ERT            INTEGER,
    BAT_ITM_NUM_ERT        INTEGER,
    INP_SRC_COD_ERT        INTEGER,
    TRN_CNT_ERT            INTEGER,
    TRN_MOR_DTA_FLG_ERT    TEXT,
    DAT_DLQ_STR_CBRS       TEXT,
    M2F_CMT_COD_CBRS       TEXT,
    HGH_DAY_DLQ_CBRS       INTEGER,
    M2F_CMP_CON_COD_CBRS   TEXT,
    HGH_AMT_DLQ_CBRS       REAL,
    PRE_COF_L1_NUM_CBRS    TEXT,
    "24_CYC_DLQ_01_CBRS"   TEXT,
    "24_CYC_DLQ_12_CBRS"   TEXT,
    "24_CYC_DLQ_23_CBRS"   TEXT,
    "24_CYC_DLQ_02_CBRS"   TEXT,
    "24_CYC_DLQ_13_CBRS"   TEXT,
    "24_CYC_DLQ_24_CBRS"   TEXT,
    "24_CYC_DLQ_03_CBRS"   TEXT,
    "24_CYC_DLQ_14_CBRS"   TEXT,
    "24_CYC_DLQ_25_CBRS"   TEXT,
    "24_CYC_DLQ_04_CBRS"   TEXT,
    "24_CYC_DLQ_15_CBRS"   TEXT,
    "24_CYC_DLQ_05_CBRS"   TEXT,
    "24_CYC_DLQ_16_CBRS"   TEXT,
    "24_CYC_DLQ_06_CBRS"   TEXT,
    "24_CYC_DLQ_17_CBRS"   TEXT,
    "24_CYC_DLQ_07_CBRS"   TEXT,
    "24_CYC_DLQ_18_CBRS"   TEXT,
    "24_CYC_DLQ_08_CBRS"   TEXT,
    "24_CYC_DLQ_19_CBRS"   TEXT,
    "24_CYC_DLQ_09_CBRS"   TEXT,
    "24_CYC_DLQ_20_CBRS"   TEXT,
    "24_CYC_DLQ_10_CBRS"   TEXT,
    "24_CYC_DLQ_21_CBRS"   TEXT,
    "24_CYC_DLQ_11_CBRS"   TEXT,
    "24_CYC_DLQ_22_CBRS"   TEXT,
    ACT_TYP_CBRS           TEXT,
    LAS_LN_BAL_CBRS        REAL,
    LAS_DAT_RPT_CBRS       TEXT,
    LAS_ACT_STA_CBRS       TEXT,
    LAS_CMT_COD_CBRS       TEXT,
    LAS_CMP_COD_CBRS       TEXT,
    CUR_PMT_RTG_CBRS       TEXT,
    PET_DAT_CBRS           TEXT,
    PET_ACT_STA_CBRS       TEXT,
    PET_SCH_PMT_AMT_CBRS   REAL,
    PET_CUR_BAL_CBRS       REAL,
    PET_AMT_PAS_DUE_CBRS   REAL,
    PET_PMT_RTG_CBRS       TEXT,
    CHX_RTG_TRN_NUM_CBRS   TEXT,
    CHX_ACT_NUM_CBRS       TEXT,
    CHX_ACT_TYP_CBRS       TEXT,
    CHX_REA_COD_1_CBRS     TEXT,
    CHX_REA_COD_2_CBRS     TEXT,
    CHX_REA_COD_3_CBRS     TEXT,
    CHX_DIS_IND_CBRS       TEXT,
    CHX_LAS_DAT_RPT_CBRS   TEXT,
    CHX_LAS_REA_RPT_CBRS   TEXT,
    CHX_LAS_CLO_STA_CBRS   TEXT,
    SEC_LAS_DAT_RPT_CBRS   TEXT,
    SEC_LN_NUM_CBRS        TEXT,
    SEC_LAS_ACT_STA_CBRS   TEXT,
    SEC_LAS_LN_BAL_CBRS    REAL
);

CREATE TABLE EXPECTED_REC_TBL (
    BK_NUM_ERT          INTEGER,
    APP_ERT             INTEGER,
    LN_NUM_ERT          TEXT,
    REF_NUM_ERT         TEXT,
    FILLER              TEXT,
    EFF_DAT_ERT         TEXT,
    TRN_COD_ERT         INTEGER,
    BAT_ITM_NUM_ERT     INTEGER,
    INP_SRC_COD_ERT     INTEGER,
    TRN_CNT_ERT         INTEGER,
    TRN_MOR_DTA_FLG_ERT TEXT,
    RCF_REF_NUM_REC     TEXT,
    RCF_FEE_DES_REC     TEXT,
    RCF_AMT_REC         REAL,
    RCF_PCT_REC         REAL,
    RCF_ASE_COD_REC     INTEGER,
    RCF_INT_IND_REC     INTEGER,
    RCF_MAX_FEE_AMT_REC REAL,
    RCF_DES_COD_REC     INTEGER,
    RCF_MAX_FEE_LTD_REC REAL,
    RCF_FEE_WVE_IND_REC TEXT,
    RCF_OGL_AMT_REC     REAL,
    EXP_PYF_IND_REC     TEXT,
    RCF_ICR_COD_REC     INTEGER,
    CST_DFC_IND_REC     TEXT,
    CMB_PMT_PTY_REC     INTEGER,
    RCF_DUE_REC         REAL,
    FCL_CST_PD_YTD_REC  REAL,
    RCF_CAP_LTD_REC     REAL,
    RCF_ASE_MTD_REC     REAL,
    RCF_PD_MTD_REC      REAL,
    RCF_ASE_YTD_REC     REAL,
    RCF_PD_YTD_REC      REAL,
    RCF_ASE_LTD_REC     REAL,
    RCF_PD_LTD_REC      REAL
);
"""


# SHAW_* staging table seed rows. 5 per table; BATCH_DATE matches the
# VALID fixture's batch date so file_to_staging gates can correlate.
_SHAW_STAGING_TABLES: List[Tuple[str, str]] = [
    ("SHAW_COLLATERAL",   "COLL"),
    ("SHAW_FEE_MASTER",   "FEE"),
    ("SHAW_LOAN_MASTER",  "LOAN"),
    ("SHAW_LOANS_NAME",   "LNAM"),
    ("SHAW_TRANSACTIONS", "TRNS"),
    ("SHAW_TRANS_MASTER", "TRMS"),
]

_SHAW_STAGING_ROWS_PER_TABLE = 5


def _shaw_raw_line(prefix: str, seq: int) -> str:
    """Return a representative ~80-char raw-line stand-in for staging seed."""
    body = f"{prefix}-{seq:04d}-VALDO-FIXTURE-2026-06-01-LN{seq:016d}-PADDING"
    return body.ljust(80)[:80]


def _staging_inserts(dialect: str) -> List[str]:
    """Emit SHAW_* INSERT statements.

    ``dialect`` is 'oracle' or 'sqlite'. Oracle uses DATE '2026-06-01';
    SQLite uses '2026-06-01' (TEXT). Quoting is identical otherwise.
    """
    rows: List[str] = []
    date_literal = "DATE '2026-06-01'" if dialect == "oracle" else "'2026-06-01'"
    for table, prefix in _SHAW_STAGING_TABLES:
        for seq in range(1, _SHAW_STAGING_ROWS_PER_TABLE + 1):
            key = f"{prefix}-{seq:04d}"
            raw = _shaw_raw_line(prefix, seq).replace("'", "''")
            rows.append(
                f"INSERT INTO {table} (BATCH_DATE, RECORD_KEY, LINE_NO, RAW_LINE) "
                f"VALUES ({date_literal}, '{key}', {seq}, '{raw}');"
            )
    return rows


def _expected_inserts(dialect: str) -> List[str]:
    """Emit one INSERT per detail row in the VALID fixture, plus BATCH_HEADER.

    Insert counts (must match build_valid_file):
      EXPECTED_BATCH_HEADER_TBL: 1
      EXPECTED_NEW1_TBL:         5
      EXPECTED_CUS_TBL:          4
      EXPECTED_ORI_TBL:          3
      EXPECTED_COD_TBL:          2
      EXPECTED_CBRS_TBL:         2
      EXPECTED_REC_TBL:          2
    """
    rows: List[str] = []
    date_literal = "DATE '2026-06-01'" if dialect == "oracle" else "'2026-06-01'"
    detail_emit = _detail_insert_oracle if dialect == "oracle" else _detail_insert_sqlite

    # BATCH_HEADER (1 row) -- mirrors header_overrides(18).
    rows.append(
        "INSERT INTO EXPECTED_BATCH_HEADER_TBL ("
        "BK_NUM_BRT, APP_BRT, EFF_DAT_BRT, TRN_COD_BRT, BAT_NUM_BRT, "
        "INP_SRC_COD_BRT, BAT_TYP_BRT, OPR_ID_BRT, "
        "ORG_LVL_NUM_1_BRT, ORG_LVL_NUM_2_BRT, ORG_LVL_NUM_3_BRT, "
        "ORG_LVL_NUM_4_BRT, ORG_LVL_NUM_5_BRT, ORG_LVL_NUM_6_BRT, "
        "ORG_LVL_NUM_7_BRT, ORG_LVL_NUM_8_BRT, ORG_LVL_NUM_9_BRT, "
        "ORG_LVL_NUM_10_BRT, ORG_LVL_NUM_11_BRT, ORG_LVL_NUM_12_BRT, "
        "ITM_CNT_BRT, DR_CR_AMT_BRT"
        f") VALUES (1, 200, {date_literal}, 'BATCH', 1, "
        "1, 32, 'VALDOTST', "
        "0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, "
        "18, 0);"
    )

    # Detail rows (5/4/3/2/2/2).
    for record_type, n in VALID_ROW_PLAN:
        for seq in range(1, n + 1):
            rows.append(detail_emit(record_type, seq))
    return rows


def build_sql_seed(dialect: str) -> str:
    """Compose the complete SQL seed file for ``dialect`` ('oracle' or 'sqlite')."""
    if dialect not in ("oracle", "sqlite"):
        raise ValueError(f"Unsupported dialect: {dialect}")
    header = _ORACLE_DDL_HEADER if dialect == "oracle" else _SQLITE_DDL_HEADER
    parts: List[str] = [header]
    parts.append("\n-- -----------------------------------------------------------------------------\n"
                 "-- 3) Seed -- SHAW_* staging tables (5 synthetic rows each).\n"
                 "-- -----------------------------------------------------------------------------\n")
    parts.extend(_staging_inserts(dialect))
    parts.append("\n-- -----------------------------------------------------------------------------\n"
                 "-- 4) Seed -- EXPECTED_*_TBL (one row per detail row in tranert_shaw_test_valid.txt).\n"
                 "--    Counts: BATCH_HEADER=1, NEW1=5, CUS=4, ORI=3, COD=2, CBRS=2, REC=2.\n"
                 "-- -----------------------------------------------------------------------------\n")
    parts.extend(_expected_inserts(dialect))
    if dialect == "oracle":
        # Oracle implicitly opens a transaction on the first DML; COMMIT
        # closes it. No BEGIN needed.
        parts.append("\nCOMMIT;\n")
        parts.append(_ORACLE_DDL_FOOTER)
    else:
        # SQLite auto-commits each statement unless a BEGIN is issued.
        # Emit nothing here -- batch the seeded INSERTs into a single
        # transaction by inserting BEGIN before the staging section. We
        # do this with a post-processing step below.
        pass
    body = "\n".join(parts) + "\n"
    if dialect == "sqlite":
        # Wrap the seed-INSERT block (everything from the first INSERT)
        # in a single transaction. CREATE TABLE statements stay
        # auto-committed (idempotent across BEGIN boundaries).
        first_insert = body.find("INSERT INTO SHAW_COLLATERAL")
        if first_insert >= 0:
            body = body[:first_insert] + "BEGIN;\n" + body[first_insert:] + "COMMIT;\n"
    return body


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def write_file(path: Path, rows: List[str], *, allow_short_lines: bool = False) -> None:
    """Write ``rows`` to ``path`` as a fixed-width file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(row + "\n")


def verify_widths(label: str, rows: List[str], width: int, *, allow_short_lines: bool = False) -> List[Tuple[int, int]]:
    """Return (line_no, actual_length) pairs that don't match ``width``."""
    bad: List[Tuple[int, int]] = []
    for i, row in enumerate(rows, start=1):
        if len(row) != width:
            bad.append((i, len(row)))
    if bad and not allow_short_lines:
        print(f"  WIDTH ERROR in {label}: {bad}", file=sys.stderr)
    return bad


def main() -> int:
    width = global_record_width()
    print(f"TRANERT global record width: {width}")

    # Build all three fixtures.
    clean = build_clean_file(width)
    valid = build_valid_file(width)
    fails = build_failure_file(width)

    # Write fixtures.
    write_file(FIXTURE_DIR / "tranert_shaw_test_clean_no_violations.txt", clean)
    write_file(FIXTURE_DIR / "tranert_shaw_test_valid.txt", valid)
    write_file(FIXTURE_DIR / "tranert_shaw_test_structural_failures.txt", fails)

    # Emit matching SQL seed files (Oracle + SQLite). These mirror the
    # VALID fixture row-for-row so L2b reconciliation finds zero
    # violations.
    SQL_DIR.mkdir(parents=True, exist_ok=True)
    oracle_sql = build_sql_seed("oracle")
    sqlite_sql = build_sql_seed("sqlite")
    (SQL_DIR / "shaw_setup.sql").write_text(oracle_sql, encoding="utf-8")
    (SQL_DIR / "shaw_setup_sqlite.sql").write_text(sqlite_sql, encoding="utf-8")

    # Verify widths.
    rc = 0
    if verify_widths("clean", clean, width):
        rc = 1
    if verify_widths("valid", valid, width):
        rc = 1
    # The failure file deliberately contains one short line.
    fail_bad = verify_widths("failures", fails, width, allow_short_lines=True)
    if len(fail_bad) != 1:
        print(
            f"  EXPECTED exactly one short line in failures file, got {len(fail_bad)}: {fail_bad}",
            file=sys.stderr,
        )
        rc = 1

    # Sanity-check seed: the number of EXPECTED_* INSERTs must match the
    # number of detail rows of each type in the VALID fixture.
    expected_insert_counts = {rt: n for rt, n in VALID_ROW_PLAN}
    for rt, n in expected_insert_counts.items():
        if n != sum(1 for row in valid[1:] if row[169:174] == TRN_COD_BY_TYPE[rt]):
            print(
                f"  SEED MISMATCH for {rt}: expected {n} INSERTs vs fixture rows",
                file=sys.stderr,
            )
            rc = 1

    print(f"  clean    -> {len(clean)} lines @ {width} chars each")
    print(f"  valid    -> {len(valid)} lines @ {width} chars each")
    print(f"  failures -> {len(fails)} lines (1 deliberately truncated)")
    print(f"  sql      -> shaw_setup.sql ({oracle_sql.count(chr(10))} lines), "
          f"shaw_setup_sqlite.sql ({sqlite_sql.count(chr(10))} lines)")
    print(f"  seed     -> EXPECTED INSERTs: BATCH_HEADER=1, "
          f"NEW1={expected_insert_counts['new1']}, CUS={expected_insert_counts['cus']}, "
          f"ORI={expected_insert_counts['ori']}, COD={expected_insert_counts['cod']}, "
          f"CBRS={expected_insert_counts['cbrs']}, REC={expected_insert_counts['rec']} "
          f"(total {1 + sum(expected_insert_counts.values())})")
    print(f"  seed     -> SHAW_* staging: 6 tables x "
          f"{_SHAW_STAGING_ROWS_PER_TABLE} rows = "
          f"{6 * _SHAW_STAGING_ROWS_PER_TABLE} synthetic rows")
    if rc == 0:
        print("OK")
    return rc


if __name__ == "__main__":
    sys.exit(main())
