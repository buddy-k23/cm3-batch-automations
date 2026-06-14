"""Extract SHAW TRANERT property-file lookups into CSVs + Oracle INSERT SQL.

Purpose
-------
The SHAW TRANERT Java mapper (``config/templates/TranertMapper (1).java``)
reads seven distinct property-file value bundles via
``CustomPropertyPlaceholderConfigurer.getProperty(...)``. The L2b SQL-Truth
gate (issue #18) reconciles the Java's output against SQL views that
themselves join against those same bundles, materialised as Oracle lookup
tables (see ``config/e2e/sources/SHAW/sql/tranert/00_bootstrap/010_lookup_tables.sql``).

This module is the bridge: it reads the live Spring ``.properties`` file,
classifies every key by its prefix, and emits two artifact sets:

1. **Seven CSVs** under ``config/e2e/sources/SHAW/lookups/``, one per lookup
   table, with stable column ordering. These are human-reviewable and
   version-controllable.
2. **One regenerated Oracle SQL file** at the path given by
   ``--sql-load-file``, containing ``TRUNCATE TABLE`` + ``INSERT INTO ...
   VALUES (...)`` statements for each lookup table. This file is consumed
   by the L2b harness's bootstrap step via :mod:`scripts.e2e_lib.sql_bootstrap`.

Idempotency
-----------
The extractor SHA-256s the input property file and writes the digest to
``<output_dir>/.sha256``. Re-running with an unchanged property file is a
no-op (no files are touched). The ``.sha256`` file is meant to be
committed alongside the CSVs so the no-op check works in CI.

Property-file format
--------------------
Java ``.properties`` subset:

* ``key=value`` lines (the ``=`` is the separator).
* ``#`` and ``!`` start line comments.
* Blank lines and lines whose first non-whitespace character is ``#`` or
  ``!`` are ignored.
* Leading and trailing whitespace on ``key`` and ``value`` are trimmed.
* Backslash line continuations and ``\\u####`` Unicode escapes are NOT
  supported. The SHAW property file uses single-line entries with ASCII
  values, so these are out of scope. If the format ever expands, add the
  features and bump the schema version.

Recognised key prefixes
-----------------------

All harness-owned tables live in the ``app_int`` Oracle schema (Policy A
per session-6 decision; ``uzapp_ad0`` is the connection account but is
NOT the default schema).

============================================================ ====================================== ============================================
Java property key prefix                                     Output CSV                             Lookup table
============================================================ ====================================== ============================================
``tranert.rep_type_ori.<prinSch>``                           ``rep_type_ori_by_prin_sch.csv``        ``app_int.LKP_VALDO_SHAW_REP_TYPE_ORI_BY_PRIN_SCH``
``tranert.base.rep_typ_uri.<termsFreq>``                     ``rep_typ_uri_by_terms_freq.csv``       ``app_int.LKP_VALDO_SHAW_REP_TYP_URI_BY_TERMS_FREQ``
``loan.master.state.cd.<bk>``                                ``loan_master_state_cd_by_bk.csv``      ``app_int.LKP_VALDO_SHAW_LOAN_MASTER_STATE_CD_BY_BK``
``tranert.ledger_cd.dept.<dept>``                            ``ledger_cd_by_dept.csv``               ``app_int.LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT``
``tranert.ledger_cd.idx3.AAA.dept.<dept>``                   ``ledger_cd_by_dept.csv`` (variant=AAA) ``app_int.LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT``
``tranert.act_typ_ui.<accountType>``                         ``act_typ_ui.csv``                      ``app_int.LKP_VALDO_SHAW_ACT_TYP_UI``
``tranert.org_level4.sap_center5.<sapCenter5>``              ``org_level4_by_sap_center5.csv``       ``app_int.LKP_VALDO_SHAW_ORG_LEVEL4_BY_SAP_CENTER5``
``tranert.32005.cif_act_code_name_relation.<nameRel>``       ``cif_act_code_by_name_relation.csv``   ``app_int.LKP_VALDO_SHAW_CIF_ACT_CODE_BY_NAME_RELATION``
============================================================ ====================================== ============================================

Keys that do not match any of these prefixes are logged at WARN and
ignored — the property file holds far more than just lookup data. Keys
that DO match a prefix but have a malformed value (e.g. ``ledger_cd.dept``
with a value missing the ``:`` separator) raise
:class:`TranertPropertyExtractError`.

CLI
---
::

    python -m scripts.e2e_lib.extract_tranert_properties \\
        --property-file /path/to/shaw.properties \\
        --output-dir    config/e2e/sources/SHAW/lookups/ \\
        --sql-load-file config/e2e/sources/SHAW/sql/tranert/10_load/010_load_lookups_from_csv.sql

All three arguments are required. AGENTS.md hard rule #3 — no env-var
defaults for resource locations.

Public API
----------
* :class:`TranertPropertyExtractError` — single exception type.
* :class:`ExtractedLookups`            — frozen value type holding every bucket.
* :func:`parse_properties`             — text → ``dict[str, str]``.
* :func:`extract_lookups`              — dict → :class:`ExtractedLookups`.
* :func:`write_csvs`                   — :class:`ExtractedLookups` → CSV files.
* :func:`write_load_sql`               — :class:`ExtractedLookups` → INSERT SQL.
* :func:`run`                          — orchestrates a full extraction with
                                         SHA-256 idempotency.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Exception type
# --------------------------------------------------------------------------- #


class TranertPropertyExtractError(ValueError):
    """Raised for any extractor misuse or malformed input.

    Per the ``scripts.e2e_lib`` house style, every failure mode raises a
    single exception type so callers match on one class. Wraps the
    offending file/key in the message for diagnostics.
    """


# --------------------------------------------------------------------------- #
# Recognised key prefixes
# --------------------------------------------------------------------------- #


#: Standard variant sentinel for the ledger_cd_by_dept table. Mirrors the
#: ``CK_LKP_LEDGER_CD_VARIANT`` check constraint declared in
#: ``00_bootstrap/010_lookup_tables.sql``.
LEDGER_CD_VARIANT_STD = "STD"

#: AAA variant sentinel for the ledger_cd_by_dept table.
LEDGER_CD_VARIANT_AAA = "AAA"

#: All seven property-file key prefixes the extractor recognises. Order is
#: significant: the table-row writers iterate in this order to keep the
#: regenerated load SQL deterministic.
_LEDGER_CD_DEPT_PREFIX = "tranert.ledger_cd.dept."
_LEDGER_CD_IDX3_AAA_DEPT_PREFIX = "tranert.ledger_cd.idx3.AAA.dept."

_PREFIX_TO_BUCKET: Tuple[Tuple[str, str], ...] = (
    ("tranert.rep_type_ori.", "rep_type_ori_by_prin_sch"),
    ("tranert.base.rep_typ_uri.", "rep_typ_uri_by_terms_freq"),
    ("loan.master.state.cd.", "loan_master_state_cd_by_bk"),
    # idx3.AAA must be checked BEFORE the plain dept prefix, because the
    # plain prefix is a strict prefix of the AAA one's first segment.
    (_LEDGER_CD_IDX3_AAA_DEPT_PREFIX, "ledger_cd_by_dept_aaa"),
    (_LEDGER_CD_DEPT_PREFIX, "ledger_cd_by_dept_std"),
    ("tranert.act_typ_ui.", "act_typ_ui"),
    ("tranert.org_level4.sap_center5.", "org_level4_by_sap_center5"),
    ("tranert.32005.cif_act_code_name_relation.", "cif_act_code_by_name_relation"),
)


#: Column ordering per CSV. The first column is always the key, followed by
#: the value column(s). The ledger_cd CSV has 4 columns because its rows
#: need (DEPT, IDX3_VARIANT, LOAN_TYPE, ACT_TYPE).
_CSV_COLUMNS: Mapping[str, Tuple[str, ...]] = {
    "rep_type_ori_by_prin_sch": ("PRIN_SCH", "REP_TYP_ORI"),
    "rep_typ_uri_by_terms_freq": ("TERMS_FREQ", "REP_TYP_URI"),
    "loan_master_state_cd_by_bk": ("BK", "STATE_CD"),
    "ledger_cd_by_dept": ("DEPT", "IDX3_VARIANT", "LOAN_TYPE", "ACT_TYPE"),
    "act_typ_ui": ("ACCOUNT_TYPE", "ACT_TYP"),
    "org_level4_by_sap_center5": ("SAP_CENTER5", "ORG_LEVEL4"),
    "cif_act_code_by_name_relation": ("NAME_RELATIONSHIP", "CIF_ACT_COD"),
}


#: Lookup-table name per CSV. Used when generating the load SQL.
#: Fully schema-qualified ``app_int.<table>`` per Policy A — harness-owned
#: tables live in the app_int schema alongside the source-system tables
#: (uzapp_ad0 is the connection account but not the default schema).
_TABLE_FOR_CSV: Mapping[str, str] = {
    "rep_type_ori_by_prin_sch": "app_int.LKP_VALDO_SHAW_REP_TYPE_ORI_BY_PRIN_SCH",
    "rep_typ_uri_by_terms_freq": "app_int.LKP_VALDO_SHAW_REP_TYP_URI_BY_TERMS_FREQ",
    "loan_master_state_cd_by_bk": "app_int.LKP_VALDO_SHAW_LOAN_MASTER_STATE_CD_BY_BK",
    "ledger_cd_by_dept": "app_int.LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT",
    "act_typ_ui": "app_int.LKP_VALDO_SHAW_ACT_TYP_UI",
    "org_level4_by_sap_center5": "app_int.LKP_VALDO_SHAW_ORG_LEVEL4_BY_SAP_CENTER5",
    "cif_act_code_by_name_relation": "app_int.LKP_VALDO_SHAW_CIF_ACT_CODE_BY_NAME_RELATION",
}


#: Final CSV-output order. The ledger_cd_by_dept_{std,aaa} intermediate
#: buckets merge into the single ``ledger_cd_by_dept`` CSV before writing.
_CSV_OUTPUT_ORDER: Tuple[str, ...] = (
    "rep_type_ori_by_prin_sch",
    "rep_typ_uri_by_terms_freq",
    "loan_master_state_cd_by_bk",
    "ledger_cd_by_dept",
    "act_typ_ui",
    "org_level4_by_sap_center5",
    "cif_act_code_by_name_relation",
)


# --------------------------------------------------------------------------- #
# Value types (frozen)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ExtractedLookups:
    """All lookup rows the extractor produced from one property file.

    Each attribute is a tuple of row tuples. Rows are sorted by the first
    column for deterministic CSV diffs and load-SQL output.

    Attributes:
        rep_type_ori_by_prin_sch: Rows of ``(PRIN_SCH, REP_TYP_ORI)``.
        rep_typ_uri_by_terms_freq: Rows of ``(TERMS_FREQ, REP_TYP_URI)``.
        loan_master_state_cd_by_bk: Rows of ``(BK, STATE_CD)``.
        ledger_cd_by_dept: Rows of ``(DEPT, IDX3_VARIANT, LOAN_TYPE, ACT_TYPE)``.
            IDX3_VARIANT is ``"STD"`` for the standard prefix and
            ``"AAA"`` for the idx3 AAA prefix. For ``"AAA"`` rows
            ACT_TYPE is ``""`` because the AAA property value is a bare
            integer, not a colon-split pair.
        act_typ_ui: Rows of ``(ACCOUNT_TYPE, ACT_TYP)``.
        org_level4_by_sap_center5: Rows of ``(SAP_CENTER5, ORG_LEVEL4)``.
        cif_act_code_by_name_relation: Rows of ``(NAME_RELATIONSHIP, CIF_ACT_COD)``.
        unmatched_keys: Property-file keys that did not match any
            recognised prefix. Exposed for diagnostics; the run logs each
            at WARN level.
    """

    rep_type_ori_by_prin_sch: Tuple[Tuple[str, ...], ...]
    rep_typ_uri_by_terms_freq: Tuple[Tuple[str, ...], ...]
    loan_master_state_cd_by_bk: Tuple[Tuple[str, ...], ...]
    ledger_cd_by_dept: Tuple[Tuple[str, ...], ...]
    act_typ_ui: Tuple[Tuple[str, ...], ...]
    org_level4_by_sap_center5: Tuple[Tuple[str, ...], ...]
    cif_act_code_by_name_relation: Tuple[Tuple[str, ...], ...]
    unmatched_keys: Tuple[str, ...] = field(default_factory=tuple)

    def rows_for(self, csv_name: str) -> Tuple[Tuple[str, ...], ...]:
        """Return the rows tuple for ``csv_name`` (the bucket name)."""
        attr = getattr(self, csv_name, None)
        if attr is None or not isinstance(attr, tuple):
            raise TranertPropertyExtractError(
                f"unknown bucket: {csv_name!r}. Valid: {sorted(_CSV_COLUMNS)}"
            )
        return attr  # type: ignore[no-any-return]


@dataclass(frozen=True)
class RunResult:
    """Result of a full :func:`run` invocation.

    Attributes:
        property_file_sha256: Hex digest of the input property file.
        rewrote: ``True`` when the property file changed and outputs were
            regenerated. ``False`` when the SHA matched and the run was a
            no-op.
        csv_files: Paths of the CSV files that exist on disk after the
            run (either freshly written or unchanged).
        load_sql_file: Path to the regenerated load-SQL file.
        unmatched_keys_count: Number of property-file keys that did not
            match any recognised prefix. Same value as
            ``len(ExtractedLookups.unmatched_keys)``.
    """

    property_file_sha256: str
    rewrote: bool
    csv_files: Tuple[Path, ...]
    load_sql_file: Path
    unmatched_keys_count: int


# --------------------------------------------------------------------------- #
# Public API: parse / extract
# --------------------------------------------------------------------------- #


def parse_properties(text: str) -> Dict[str, str]:
    """Parse Java ``.properties`` text into a key/value dict.

    Subset supported (see module docstring): ``key=value``, ``#``/``!``
    comments, blank lines. No line continuations, no Unicode escapes.

    Duplicate keys: later definitions win, matching Java's
    ``Properties.load`` behaviour.

    Args:
        text: The full property-file text.

    Returns:
        Mapping of key to value, both trimmed.

    Raises:
        TranertPropertyExtractError: If a non-comment, non-blank line has
            no ``=`` separator.
    """
    result: Dict[str, str] = {}
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("!"):
            continue
        if "=" not in line:
            raise TranertPropertyExtractError(
                f"property file line {lineno}: no '=' separator: {raw_line!r}"
            )
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def extract_lookups(props: Mapping[str, str]) -> ExtractedLookups:
    """Classify property keys into the seven lookup buckets.

    Order of operations per property entry:

    1. Find the first matching prefix in declaration order (idx3.AAA
       checked before the plain dept prefix).
    2. Strip the prefix to get the lookup key.
    3. Apply per-bucket value parsing (colon-split for ledger_cd, plain
       string for the rest).
    4. Append a row to the corresponding bucket.

    Args:
        props: Parsed property mapping from :func:`parse_properties`.

    Returns:
        :class:`ExtractedLookups` with sorted-by-first-column rows.

    Raises:
        TranertPropertyExtractError: If a ledger_cd dept value has a
            malformed colon-split shape (not exactly two segments).
    """
    buckets: Dict[str, List[Tuple[str, ...]]] = {name: [] for name in _CSV_COLUMNS}
    # Intermediate buckets that get merged into ledger_cd_by_dept.
    ledger_std: List[Tuple[str, ...]] = []
    ledger_aaa: List[Tuple[str, ...]] = []
    unmatched: List[str] = []

    for key, value in props.items():
        matched = False
        for prefix, bucket_name in _PREFIX_TO_BUCKET:
            if not key.startswith(prefix):
                continue
            lookup_key = key[len(prefix) :]
            if not lookup_key:
                # e.g. "tranert.rep_type_ori." with nothing after — malformed.
                raise TranertPropertyExtractError(
                    f"property key {key!r}: prefix {prefix!r} has no value segment"
                )
            if bucket_name == "ledger_cd_by_dept_std":
                loan_type, act_type = _split_colon_pair(key, value)
                ledger_std.append(
                    (lookup_key, LEDGER_CD_VARIANT_STD, loan_type, act_type)
                )
            elif bucket_name == "ledger_cd_by_dept_aaa":
                # AAA variant value is a bare integer per
                # TranertMapper line 414 (Integer.valueOf(...)).
                ledger_aaa.append((lookup_key, LEDGER_CD_VARIANT_AAA, value, ""))
            else:
                buckets[bucket_name].append((lookup_key, value))
            matched = True
            break
        if not matched:
            unmatched.append(key)

    # Merge ledger_std + ledger_aaa into ledger_cd_by_dept, sorted by
    # (DEPT, IDX3_VARIANT) so STD precedes AAA per the variant order
    # declared in 010_lookup_tables.sql comments.
    merged_ledger = sorted(ledger_std + ledger_aaa, key=lambda r: (r[0], r[1]))
    buckets["ledger_cd_by_dept"] = merged_ledger

    # Sort every other bucket by its first column.
    for name in _CSV_COLUMNS:
        if name == "ledger_cd_by_dept":
            continue
        buckets[name].sort(key=lambda r: r[0])

    return ExtractedLookups(
        rep_type_ori_by_prin_sch=tuple(buckets["rep_type_ori_by_prin_sch"]),
        rep_typ_uri_by_terms_freq=tuple(buckets["rep_typ_uri_by_terms_freq"]),
        loan_master_state_cd_by_bk=tuple(buckets["loan_master_state_cd_by_bk"]),
        ledger_cd_by_dept=tuple(buckets["ledger_cd_by_dept"]),
        act_typ_ui=tuple(buckets["act_typ_ui"]),
        org_level4_by_sap_center5=tuple(buckets["org_level4_by_sap_center5"]),
        cif_act_code_by_name_relation=tuple(buckets["cif_act_code_by_name_relation"]),
        unmatched_keys=tuple(unmatched),
    )


def _split_colon_pair(key: str, value: str) -> Tuple[str, str]:
    """Split a ``loan_type:act_type`` value, validating the shape.

    Args:
        key: The property key (for error messages).
        value: The raw property value.

    Returns:
        ``(loan_type, act_type)`` tuple, both trimmed.

    Raises:
        TranertPropertyExtractError: If the value does not split into
            exactly two segments on ``:``.
    """
    parts = value.split(":")
    if len(parts) != 2:
        raise TranertPropertyExtractError(
            f"property key {key!r}: value {value!r} must be 'loan_type:act_type' "
            f"(exactly two colon-separated segments), got {len(parts)} segment(s)"
        )
    return parts[0].strip(), parts[1].strip()


# --------------------------------------------------------------------------- #
# Public API: write CSVs and load SQL
# --------------------------------------------------------------------------- #


def write_csvs(lookups: ExtractedLookups, output_dir: Path) -> Tuple[Path, ...]:
    """Write the seven CSV files for ``lookups`` under ``output_dir``.

    Each CSV has a header row from :data:`_CSV_COLUMNS` and body rows
    sorted by the first column. Writes use ``newline=""`` and
    ``QUOTE_MINIMAL`` so the output is byte-identical across Linux and
    Windows (one ``\\n`` line ending, no spurious CR insertions).

    Args:
        lookups: Extracted rows.
        output_dir: Directory under which to write. Created if missing.

    Returns:
        Tuple of paths written, in :data:`_CSV_OUTPUT_ORDER`.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for csv_name in _CSV_OUTPUT_ORDER:
        path = output_dir / f"{csv_name}.csv"
        rows = lookups.rows_for(csv_name)
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
            writer.writerow(_CSV_COLUMNS[csv_name])
            for row in rows:
                writer.writerow(row)
        paths.append(path)
    return tuple(paths)


def write_load_sql(lookups: ExtractedLookups, sql_path: Path) -> None:
    """Write the regenerated Oracle load SQL.

    The file is auto-generated and must not be hand-edited. It begins
    with a fixed banner comment, then per lookup table:

    * One ``TRUNCATE TABLE`` to clear existing rows (idempotency).
    * One ``INSERT INTO ... VALUES (...)`` per row, with values
      single-quoted and any embedded single quotes doubled per Oracle
      string-literal rules.

    Bind variables are not used here: the values come from the
    version-controlled property file, not from user input. AGENTS.md
    hard rule on SQL bind variables targets values that originate from
    user input or runtime context (which is why the per-record-type
    ``expected_*.sql`` files in :file:`20_query/` must use binds when
    they reference batch dates etc.); auto-generated lookup-table
    seeding from a versioned source file is the standard exception.

    Statement terminators
    ---------------------
    Each statement is followed by ``;`` on its own line. This is the
    terminator shape :func:`scripts.e2e_lib.sql_bootstrap._split_statements`
    recognises — an inline trailing semicolon on the same line as the
    statement body would NOT split (the splitter requires ``;`` as the
    only non-whitespace content on a line).

    Cross-OS byte determinism
    -------------------------
    The file is written with ``newline=""`` so Python does not translate
    ``\\n`` to ``\\r\\n`` on Windows. The SHA-256 idempotency check
    depends on byte-stable output across platforms.

    Args:
        lookups: Extracted rows.
        sql_path: Path to write. Parent directory is created if missing.
    """
    sql_path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = [
        "-- ----------------------------------------------------------------",
        "-- AUTO-GENERATED BY scripts/e2e_lib/extract_tranert_properties.py",
        "-- DO NOT EDIT BY HAND. Re-run the extractor to regenerate.",
        "-- ----------------------------------------------------------------",
        "-- This file is consumed by scripts/e2e_lib/sql_bootstrap.py during",
        "-- the L2b SHAW TRANERT gate's load phase. Each lookup table is",
        "-- TRUNCATEd then re-INSERTed so the harness can re-run cleanly.",
        "-- Bind variables are not used here because the values come from a",
        "-- version-controlled property file, not from user input.",
        "-- Each statement is terminated by ';' on its own line so the",
        "-- bootstrap statement splitter parses every TRUNCATE and INSERT",
        "-- as an independent statement.",
        "-- ----------------------------------------------------------------",
        "",
    ]
    for csv_name in _CSV_OUTPUT_ORDER:
        table = _TABLE_FOR_CSV[csv_name]
        rows = lookups.rows_for(csv_name)
        columns = _CSV_COLUMNS[csv_name]
        lines.append(f"-- {table} ({len(rows)} row(s))")
        lines.append(f"TRUNCATE TABLE {table}")
        lines.append(";")
        # When the bucket is empty, the per-table header comment above
        # already documents the "0 row(s)" state. Emitting an additional
        # "-- (no rows produced)" comment would risk creating a
        # comment-only trailing statement after the splitter discards the
        # final TRUNCATE's ';' terminator, which Oracle would reject as
        # ORA-00900.
        for row in rows:
            quoted_values = ", ".join(_quote_oracle_string(v) for v in row)
            column_list = ", ".join(columns)
            lines.append(
                f"INSERT INTO {table} ({column_list}) VALUES ({quoted_values})"
            )
            lines.append(";")
        lines.append("")
    with sql_path.open("w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))


def _quote_oracle_string(value: str) -> str:
    """Quote a string for an Oracle ``VALUES`` literal.

    Empty strings are emitted as NULL because Oracle treats ``''`` as
    NULL; emitting ``''`` would be misleading.

    Args:
        value: The raw string value.

    Returns:
        An Oracle SQL literal (either ``NULL`` or a single-quoted
        string with any embedded single quotes doubled).
    """
    if value == "":
        return "NULL"
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


# --------------------------------------------------------------------------- #
# Public API: orchestrating run with SHA-256 idempotency
# --------------------------------------------------------------------------- #


def run(
    *,
    property_file: Path,
    output_dir: Path,
    sql_load_file: Path,
    force: bool = False,
) -> RunResult:
    """Drive a full extraction with SHA-256 idempotency.

    1. Read the property file's bytes and compute its SHA-256.
    2. If ``<output_dir>/.sha256`` exists and matches and ``force`` is
       ``False``, return ``RunResult(rewrote=False)`` without touching
       any output files.
    3. Otherwise parse, extract, write the 7 CSVs, write the load SQL,
       and update the ``.sha256`` file.

    Args:
        property_file: Path to the live Spring ``.properties`` file.
        output_dir: Directory under which to write the seven CSVs and
            the ``.sha256`` fingerprint file.
        sql_load_file: Path to the regenerated load-SQL file.
        force: When ``True``, regenerate output even if the SHA matches.
            Useful for one-shot remediation after the column-shape
            convention changes.

    Returns:
        :class:`RunResult` describing what was (or wasn't) regenerated.

    Raises:
        TranertPropertyExtractError: If the property file is missing or
            malformed.
    """
    if not property_file.is_file():
        raise TranertPropertyExtractError(f"property file not found: {property_file}")

    text = property_file.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    sha_path = output_dir / ".sha256"
    if not force and sha_path.is_file():
        existing = sha_path.read_text(encoding="utf-8").strip()
        if existing == digest:
            logger.info(
                "tranert_property_extract: no change (sha256=%s); skipping write",
                digest,
            )
            csv_paths = tuple(output_dir / f"{name}.csv" for name in _CSV_OUTPUT_ORDER)
            return RunResult(
                property_file_sha256=digest,
                rewrote=False,
                csv_files=csv_paths,
                load_sql_file=sql_load_file,
                unmatched_keys_count=0,
            )

    props = parse_properties(text)
    lookups = extract_lookups(props)

    if lookups.unmatched_keys:
        logger.warning(
            "tranert_property_extract: %d property-file key(s) did not match "
            "any recognised prefix and were ignored; sample: %s",
            len(lookups.unmatched_keys),
            list(lookups.unmatched_keys[:5]),
        )

    csv_paths = write_csvs(lookups, output_dir)
    write_load_sql(lookups, sql_load_file)
    output_dir.mkdir(parents=True, exist_ok=True)
    sha_path.write_text(digest + "\n", encoding="utf-8")

    logger.info(
        "tranert_property_extract: regenerated %d CSVs and load SQL (sha256=%s)",
        len(csv_paths),
        digest,
    )
    return RunResult(
        property_file_sha256=digest,
        rewrote=True,
        csv_files=csv_paths,
        load_sql_file=sql_load_file,
        unmatched_keys_count=len(lookups.unmatched_keys),
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns 0 on success, 2 on extractor error."""
    parser = argparse.ArgumentParser(
        prog="extract_tranert_properties",
        description=(
            "Extract SHAW TRANERT lookup tables from a Spring .properties "
            "file into CSVs + Oracle INSERT SQL."
        ),
    )
    parser.add_argument(
        "--property-file",
        required=True,
        type=Path,
        help="Path to the Spring .properties file (read-only).",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory under which the seven CSVs and .sha256 are written.",
    )
    parser.add_argument(
        "--sql-load-file",
        required=True,
        type=Path,
        help="Path to the regenerated load-SQL file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate output even if the SHA-256 fingerprint matches.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Set the module logger to INFO (default WARNING).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        result = run(
            property_file=args.property_file,
            output_dir=args.output_dir,
            sql_load_file=args.sql_load_file,
            force=args.force,
        )
    except TranertPropertyExtractError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    if result.rewrote:
        sys.stdout.write(
            f"regenerated {len(result.csv_files)} CSVs + load SQL "
            f"(sha256={result.property_file_sha256})\n"
        )
    else:
        sys.stdout.write(
            f"no change (sha256={result.property_file_sha256}); "
            f"{len(result.csv_files)} CSV(s) and load SQL left untouched\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
