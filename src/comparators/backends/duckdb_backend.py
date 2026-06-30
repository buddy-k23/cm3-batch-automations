"""DuckDB-backed comparison backend (S25-2 contract + S25-3 native-read fast path).

This backend computes the file-to-file diff with DuckDB SQL (a vectorized,
columnar OLAP engine) while emitting the **exact same materialized result dict**
as the default
:class:`~src.comparators.backends.native_backend.NativeComparisonBackend`.  It is
a drop-in for the delimited keyed comparison that powers the DB↔file / Excel use
case, and is selected via ``COMPARISON_BACKEND=duckdb`` or
``get_comparison_backend("duckdb")``.

Two execution paths, one contract
---------------------------------
The result dict is identical no matter which path runs; the path is chosen
internally for performance/correctness:

1. **Native-read fast path (S25-3)** — the perf win.  For a *header-keyed
   delimited* file (the db-compare / excel / large regime: ``.txt`` / ``.dat`` /
   ``.psv`` → ``|``, plus any delimited file whose key columns live in a header
   row) **both files are read with DuckDB's native ``read_csv``** —
   ``all_varchar=true`` (no type inference, so ``'007'`` stays ``'007'``), the
   delimiter chosen to match native's ``'|'`` rule, ``header=true`` matching
   native's header re-read, and ``NULL → ''`` via SQL ``COALESCE`` to match
   pandas' ``keep_default_na=False``.  The **entire diff** (inner join, anti-join
   only-in, per-row field analysis, ``field_statistics``) runs in SQL/DuckDB —
   the bulk data is **never parsed through pandas**.  This is the path that
   recovers the benchmark's out-of-core / 100× advantage (the S25-2 path lost it
   by routing every byte through pandas first).  Only the small header line is
   read with pandas (``nrows=0``) to discover the exact native column names.

2. **Pandas-parse fallback (the S25-2 path)** — used **only** where native-read
   cannot guarantee byte-parity.  Here both files are parsed through the *exact*
   native stages (:class:`FormatDetector` → parser → header/mapping
   key-resolution fallback), the resolved pandas frames are *registered* into
   DuckDB, and the same SQL diff runs over them.  Consuming the identical frames
   native's ``FileComparator`` would consume eliminates any divergence by
   construction.  The fast path falls back here automatically when the DuckDB
   ``read_csv`` raises (e.g. ragged rows — both engines actually error, and the
   fallback reproduces native's error type) or when the keys resolve only via the
   integer-column / mapping path rather than a header row.

Cases the native engine handles through pandas-specific parsing — **fixed-width**
inputs, **row-by-row** (no keys) comparison, the **chunked** set-based path, and
any input where the keys cannot be resolved — are **cleanly deferred to the
native backend**.  Structure-incompatible inputs reproduce the native
early-return dict exactly.

Regime → path summary
---------------------
====================================  ===========================================
Input regime                          Path
====================================  ===========================================
Header-keyed pipe/psv/txt/dat,        **native-read fast path** (no pandas bulk)
parseable rows
Header-keyed delimited but ragged     pandas-parse fallback (native error
rows (engines error)                  reproduced)
Keys only resolvable via integer      pandas-parse fallback
columns / mapping names
Header-keyed ``.csv`` (comma)         **defer to native** (native is *broken* —
                                      re-reads sep='|'; we replicate, see below)
Fixed-width / no keys / use_chunked   defer to native
Structure-incompatible                native early-return dict reproduced
====================================  ===========================================

Semantics reproduced
--------------------
- All values are compared as **text**; native reads with ``dtype=str`` /
  ``keep_default_na=False`` (NULL → ``''``).  The fast path forces the same with
  ``all_varchar=true`` + ``COALESCE(col,'')``; the fallback registers the frames
  as-is.  ``string_analysis`` lengths therefore match exactly.
- A **difference** = a key present on both sides with at least one non-key value
  column unequal.  **only_in** = a key present on exactly one side.
  **matching** = rows in file1 minus only-in-file1 minus rows-with-differences
  (the native arithmetic).
- ``source_row_file1`` / ``source_row_file2`` reproduce native's value.

Resolved parity edge cases (S25-2 flags)
----------------------------------------
1. **Duplicate key values** — RESOLVED.  Native's pandas inner-merge emits the
   full cartesian product of the duplicate-keyed rows, ordered by file1's
   physical row order (outer) then file2's physical row order (inner); the
   ``source_row_file1`` / ``source_row_file2`` on each cartesian row are the two
   physical row numbers.  The fast path reproduces this **exactly** by carrying a
   per-side row-order column and ``ORDER BY f1_row, f2_row`` on the join, using
   those row numbers as the source rows.  (When native used the header re-read it
   has no ``__source_row__`` column and falls back to the merged-frame position
   for *both* sides — the fast path reproduces *that* too, because for the header
   regime the physical row number equals the merged position when there are no
   duplicate keys, and for duplicates the cartesian ``(rn1, rn2)`` enumeration is
   what native's positional ``idx+1`` walks.)  See
   ``tests/unit/test_duckdb_parity_matrix.py::test_duplicate_key_source_rows_canonicalized``.
2. **Header-keyed ``.csv``** — native is *broken* (re-reads with ``sep='|'``,
   mis-parses the comma file, raises in the merge).  Per "replicate don't
   improve", the fast path is **not** taken for comma ``.csv``; the case is
   deferred to native and raises native's identical error.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd

from src.comparators.backends.base import ComparisonBackend
from src.comparators.backends.native_backend import (
    NativeComparisonBackend,
    _check_structure_compatibility,
)
from src.comparators.file_comparator import FileComparator
from src.parsers.fixed_width_parser import FixedWidthParser
from src.parsers.format_detector import FormatDetector

_INSTALL_HINT = (
    "DuckDB backend selected but the 'duckdb' package is not installed; "
    "pip install duckdb"
)

# Extensions for which native's header re-read uses ``sep='|'``.  The
# native-read fast path is only safe for these (and the historic pipe default)
# because native itself reads header keys with a pipe separator — a comma
# ``.csv`` is therefore *mis-parsed* by native and must be deferred so we
# reproduce that (broken) behaviour rather than "fix" it.
_PIPE_NATIVE_EXTENSIONS = frozenset({".txt", ".dat", ".psv", ""})


class DuckDBComparisonBackend(ComparisonBackend):
    """Comparison backend that computes the diff with DuckDB SQL.

    Reproduces the native in-memory result contract exactly for delimited, keyed
    comparisons.  Uses a DuckDB native-``read_csv`` fast path (no pandas bulk
    parse) for the header-keyed pipe-delimited regime, a pandas-parse fallback
    where native-read cannot guarantee parity, and defers the non-delimited /
    non-keyed / chunked cases to :class:`NativeComparisonBackend`.
    """

    def compare(
        self,
        file1: str,
        file2: str,
        key_columns: list[str] | None,
        *,
        mapping_config: dict[str, Any] | None = None,
        detailed: bool = True,
        chunk_size: int = 100000,
        progress: bool = False,
        use_chunked: bool = False,
    ) -> dict[str, Any]:
        """Compare two delimited files with DuckDB and return the native contract.

        See :meth:`src.comparators.backends.base.ComparisonBackend.compare` for
        the full contract.  Chooses internally between the native-read fast path,
        the pandas-parse fallback, and deferral to native (see module docstring);
        the returned dict is identical regardless of path.

        Args:
            file1: Path to the first delimited file.
            file2: Path to the second delimited file.
            key_columns: Key column names for row matching.  Required — passing
                ``None`` raises ``ValueError`` (the DuckDB engine targets keyed
                comparison; row-by-row is a native concern).
            mapping_config: Optional mapping dict.  When the input is fixed-width
                the whole comparison is deferred to native.
            detailed: When True, include the field-level diff analysis
                (``type`` + ``string_analysis``) and ``field_statistics``; when
                False the diff list carries only ``file1`` / ``file2`` and
                ``field_statistics`` is ``{}`` — matching native.
            chunk_size: Forwarded to native when deferring the chunked path.
            progress: Forwarded to native when deferring the chunked path.
            use_chunked: When True the set-based path is deferred to native.

        Returns:
            The comparison result dict (native in-memory shape).

        Raises:
            ImportError: If the ``duckdb`` package is not installed.
            ValueError: If ``key_columns`` is empty/``None``.
        """
        # Lazy, actionable optional-dependency import.  Kept inside the method so
        # the module imports fine on a duckdb-less interpreter.
        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
            raise ImportError(_INSTALL_HINT) from exc

        # --- Deferral cases: hand off to native for guaranteed parity --------
        if use_chunked:
            return NativeComparisonBackend().compare(
                file1, file2, key_columns,
                mapping_config=mapping_config, detailed=detailed,
                chunk_size=chunk_size, progress=progress, use_chunked=True,
            )

        if not key_columns:
            # The native engine would fall back to positional row-by-row here;
            # the DuckDB keyed engine requires keys.  Raise the same error type.
            raise ValueError(
                "DuckDB backend requires key_columns for comparison; "
                "row-by-row (no keys) comparison is handled by the native backend."
            )

        detector = FormatDetector()
        if (
            detector.get_parser_class(file1) is FixedWidthParser
            or detector.get_parser_class(file2) is FixedWidthParser
        ):
            # Fixed-width parsing is pandas/byte-offset specific; defer to native
            # so the fixed-width contract is reproduced exactly.
            return NativeComparisonBackend().compare(
                file1, file2, key_columns,
                mapping_config=mapping_config, detailed=detailed,
                chunk_size=chunk_size, progress=progress, use_chunked=False,
            )

        # --- S25-3 NATIVE-READ FAST PATH -------------------------------------
        # Try the no-pandas-bulk path first for the header-keyed pipe regime.
        # It returns the full result dict on success, or None to signal "cannot
        # guarantee parity — use the fallback" (the only correctness-safe action).
        fast = self._try_native_read_fast_path(
            duckdb, file1, file2, key_columns, detailed
        )
        if fast is not None:
            return fast

        # --- Pandas-parse FALLBACK (the S25-2 path) --------------------------
        # Reproduce the native parse + structure check + key resolution, register
        # the resolved frames, and run the same SQL diff over them.
        df1, df2 = self._native_parse(detector, file1, file2, mapping_config)

        structure_errors = _check_structure_compatibility(df1, df2, mapping_config)
        if structure_errors:
            return self._structure_incompatible_result(df1, df2, structure_errors)

        df1, df2, keys_resolved = self._resolve_keys(
            df1, df2, file1, file2, key_columns, mapping_config
        )
        if not keys_resolved:
            # Native has further pandas fallbacks (or fails the merge) when the
            # keys cannot be resolved to columns.  Defer to native to reproduce
            # whatever it does (including any error) exactly.
            return NativeComparisonBackend().compare(
                file1, file2, key_columns,
                mapping_config=mapping_config, detailed=detailed,
                chunk_size=chunk_size, progress=progress, use_chunked=False,
            )

        # Source-row semantics mirror native's: when the resolved frames carry
        # ``__source_row__`` (the integer-column / mapping parse) native uses each
        # side's *physical* row number; when they don't (the header re-read)
        # native uses the merged-frame *position* for both sides.  Choose the
        # matching mode so source_row reproduces native value-for-value.
        physical_rows = "__source_row__" in df1.columns
        columns = [c for c in df1.columns if c != "__source_row__"]
        df1_q = self._with_row_order(df1, columns, physical_rows)
        df2_q = self._with_row_order(df2, columns, physical_rows)

        con = duckdb.connect(database=":memory:")
        try:
            con.register("f1", df1_q)
            con.register("f2", df2_q)
            return self._compare_with_sql(
                con, columns, key_columns, detailed,
                coalesce=False, physical_source_rows=physical_rows,
            )
        finally:
            con.close()

    # ------------------------------------------------------------------
    # S25-3 native-read fast path
    # ------------------------------------------------------------------
    def _try_native_read_fast_path(
        self,
        duckdb: Any,
        file1: str,
        file2: str,
        key_columns: list[str],
        detailed: bool,
    ) -> dict[str, Any] | None:
        """Attempt the DuckDB-native-read path; return the result or ``None``.

        Reads BOTH files with DuckDB ``read_csv`` (no pandas bulk parse) and runs
        the whole diff in SQL.  Returns the full native-shaped result dict on
        success, or ``None`` when the path cannot guarantee parity and the caller
        must use the pandas-parse fallback.  ``None`` is returned (never an
        exception) for every "not applicable / not safe" condition so the caller
        degrades cleanly — correctness over speed.

        Args:
            duckdb: The imported ``duckdb`` module.
            file1: First file path.
            file2: Second file path.
            key_columns: Requested key column names.
            detailed: Include field-level analysis and ``field_statistics``.

        Returns:
            The result dict, or ``None`` to fall back.
        """
        delim = self._native_read_delimiter(file1, file2)
        if delim is None:
            # Comma ``.csv`` (native mis-parses with sep='|') or mixed/unknown
            # extensions — not safe to native-read; let the fallback/defer handle.
            return None

        # Discover the EXACT native column names via pandas' header-only read
        # (cheap: header line only, nrows=0).  Using pandas here guarantees the
        # header names match native's header re-read byte-for-byte (quoted header
        # names, etc.) so structure-check and key-resolution decisions agree.
        try:
            cols1 = list(
                pd.read_csv(
                    file1, sep=delim, dtype=str, keep_default_na=False,
                    header=0, nrows=0,
                ).columns
            )
            cols2 = list(
                pd.read_csv(
                    file2, sep=delim, dtype=str, keep_default_na=False,
                    header=0, nrows=0,
                ).columns
            )
        except Exception:
            return None

        # Keys must live in the header on BOTH sides — otherwise native would use
        # the integer-column / mapping resolution path; defer to the fallback.
        if not (
            all(k in cols1 for k in key_columns)
            and all(k in cols2 for k in key_columns)
        ):
            return None

        # Structure compatibility is a *pre-key-resolution* concern native
        # evaluates on its headerless integer parse (column counts include the
        # ``__source_row__`` column and the header line counts as a data row).
        # The fast path cannot cheaply reproduce that headerless early-return
        # shape, so any structural difference between the two headers (different
        # column set or order) is handed to the pandas-parse fallback, which
        # reproduces native's integer-parse structure dict exactly.  The fast path
        # therefore runs only when both headers are identical.
        if cols1 != cols2:
            return None

        con = duckdb.connect(database=":memory:")
        try:
            try:
                self._register_native_read(con, "f1", file1, delim, cols1)
                self._register_native_read(con, "f2", file2, delim, cols2)
                # Force the read to execute now so a ragged-row / parse error
                # surfaces here (and we fall back) rather than mid-diff.
                total1 = con.execute("SELECT COUNT(*) FROM f1").fetchone()[0]
                total2 = con.execute("SELECT COUNT(*) FROM f2").fetchone()[0]
            except Exception:
                # DuckDB read_csv raised (e.g. ragged rows).  Native's pandas
                # read also raises on this input; fall back so the native error
                # type is reproduced exactly instead of leaking a DuckDB error.
                return None

            # Header regime: native used the header re-read (no __source_row__),
            # so source_row is the merged-frame position for BOTH sides — not the
            # physical row number.  physical_source_rows=False selects that.
            return self._compare_with_sql(
                con, cols1, key_columns, detailed, coalesce=True,
                total1=total1, total2=total2, physical_source_rows=False,
            )
        finally:
            con.close()

    @staticmethod
    def _native_read_delimiter(file1: str, file2: str) -> str | None:
        """Return the pipe delimiter when both files are native-read-safe.

        The native-read fast path is only correct for files native itself would
        read with ``sep='|'`` (``.txt`` / ``.dat`` / ``.psv`` / no extension).  A
        comma ``.csv`` is *mis-parsed* by native's header re-read, so it must be
        deferred (return ``None``) to reproduce that broken behaviour.

        Args:
            file1: First file path.
            file2: Second file path.

        Returns:
            ``'|'`` when both files are pipe-native-safe, else ``None``.
        """
        ext1 = os.path.splitext(file1)[1].lower()
        ext2 = os.path.splitext(file2)[1].lower()
        if ext1 in _PIPE_NATIVE_EXTENSIONS and ext2 in _PIPE_NATIVE_EXTENSIONS:
            return "|"
        return None

    @staticmethod
    def _register_native_read(
        con: Any, name: str, path: str, delim: str, columns: list[str]
    ) -> None:
        """Register a DuckDB ``read_csv`` view reading *path* with native parity.

        Reads with ``all_varchar=true`` (no type inference — ``'007'`` stays
        ``'007'``), the native delimiter, ``header=true``, and the explicit
        column names discovered from the header so the column set matches native
        exactly.  NULL → ``''`` coalescing is applied at diff time, not here, so
        empty fields surface as DuckDB NULL and are normalized uniformly.

        Args:
            con: Open DuckDB connection.
            name: View name to create (``f1`` / ``f2``).
            path: File path to read.
            delim: Field delimiter.
            columns: Explicit column names (header order).
        """
        col_spec = ", ".join(
            "'" + c.replace("'", "''") + "': 'VARCHAR'" for c in columns
        )
        path_lit = path.replace("'", "''")
        delim_lit = delim.replace("'", "''")
        reader = (
            f"read_csv('{path_lit}', delim='{delim_lit}', header=true, "
            f"all_varchar=true, auto_detect=false, "
            f"strict_mode=true, columns={{{col_spec}}})"
        )
        # Carry a 1-based physical row-order column so the SQL diff can reproduce
        # native's source-row enumeration (incl. duplicate-key cartesian order).
        con.execute(
            f"CREATE VIEW {name} AS "
            f"SELECT *, row_number() OVER () AS __row__ FROM {reader}"
        )

    # ------------------------------------------------------------------
    # Native-parity parsing helpers (pandas-parse fallback)
    # ------------------------------------------------------------------
    @staticmethod
    def _native_parse(
        detector: FormatDetector,
        file1: str,
        file2: str,
        mapping_config: dict[str, Any] | None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Parse both files exactly as the native delimited path does.

        Args:
            detector: Shared :class:`FormatDetector`.
            file1: First file path.
            file2: Second file path.
            mapping_config: Optional mapping (unused for delimited; kept for
                signature symmetry with the native path).

        Returns:
            Tuple of the two parsed DataFrames.
        """
        parser1 = detector.get_parser_class(file1)(file1)
        parser2 = detector.get_parser_class(file2)(file2)
        return parser1.parse(), parser2.parse()

    @staticmethod
    def _resolve_keys(
        df1: pd.DataFrame,
        df2: pd.DataFrame,
        file1: str,
        file2: str,
        key_columns: list[str],
        mapping_config: dict[str, Any] | None,
    ) -> tuple[pd.DataFrame, pd.DataFrame, bool]:
        """Resolve key columns to named columns, mirroring the native fallback.

        Args:
            df1: First parsed DataFrame (integer columns + ``__source_row__``).
            df2: Second parsed DataFrame.
            file1: First file path (for the header/mapping re-read).
            file2: Second file path.
            key_columns: Requested key column names.
            mapping_config: Optional mapping providing field names for headerless
                files.

        Returns:
            Tuple ``(df1, df2, resolved)`` where ``resolved`` is True when every
            key column is present in both returned frames.
        """
        if all(k in df1.columns for k in key_columns) and all(
            k in df2.columns for k in key_columns
        ):
            return df1, df2, True

        try:
            df1h = pd.read_csv(file1, sep="|", dtype=str, keep_default_na=False, header=0)
            df2h = pd.read_csv(file2, sep="|", dtype=str, keep_default_na=False, header=0)
            if all(k in df1h.columns for k in key_columns) and all(
                k in df2h.columns for k in key_columns
            ):
                # Native's header re-read carries NO __source_row__, so source_row
                # falls back to the merged-frame position.  Return the frames
                # as-is to reproduce that exactly.
                return df1h, df2h, True
            if mapping_config and mapping_config.get("fields"):
                names = [
                    f.get("name")
                    for f in mapping_config.get("fields", [])
                    if f.get("name")
                ]
                if names:
                    df1m = pd.read_csv(
                        file1, sep="|", dtype=str, keep_default_na=False,
                        header=None, names=names,
                    )
                    df2m = pd.read_csv(
                        file2, sep="|", dtype=str, keep_default_na=False,
                        header=None, names=names,
                    )
                    # Native's mapping re-read also carries NO __source_row__.
                    if all(k in df1m.columns for k in key_columns) and all(
                        k in df2m.columns for k in key_columns
                    ):
                        return df1m, df2m, True
        except Exception:
            pass

        return df1, df2, False

    @staticmethod
    def _with_row_order(
        df: pd.DataFrame, columns: list[str], physical_rows: bool
    ) -> pd.DataFrame:
        """Project *columns* and append a 1-based ``__row__`` ordering column.

        ``__row__`` always carries the frame's physical row order (the join is
        ordered by it on both sides so the duplicate-key cartesian product is
        enumerated in pandas' inner-merge order).  When *physical_rows* is True
        the value is the authoritative ``__source_row__`` (used as the emitted
        source_row); otherwise it is the positional index (and the emitted
        source_row is the merged-frame position instead — see
        :meth:`_compare_with_sql`).

        Args:
            df: Resolved frame (carries ``__source_row__`` iff *physical_rows*).
            columns: Value/key column projection (excludes ``__source_row__``).
            physical_rows: Whether ``__source_row__`` is the row-order source.

        Returns:
            A frame with the projected columns plus ``__row__``.
        """
        out = df[columns].copy()
        if physical_rows and "__source_row__" in df.columns:
            out["__row__"] = df["__source_row__"].astype(int).to_numpy()
        else:
            out["__row__"] = range(1, len(out) + 1)
        return out

    # ------------------------------------------------------------------
    # SQL-driven diff + materialization (shared by both paths)
    # ------------------------------------------------------------------
    def _compare_with_sql(
        self,
        con: Any,
        columns: list[str],
        key_columns: list[str],
        detailed: bool,
        *,
        coalesce: bool,
        physical_source_rows: bool,
        total1: int | None = None,
        total2: int | None = None,
    ) -> dict[str, Any]:
        """Run the SQL diff and materialize the native-shaped result dict.

        Uses an ``INNER JOIN`` on the key columns for matched rows, ordered by
        ``(f1.__row__, f2.__row__)`` so the duplicate-key cartesian product is
        enumerated in pandas' inner-merge order.  Anti-joins (``WHERE NOT
        EXISTS``) yield ``only_in_file1`` / ``only_in_file2``.

        ``source_row_file*`` reproduces native's two distinct conventions:

        - *physical_source_rows=True* (the integer-column / mapping parse, which
          carries ``__source_row__``): each side's emitted source row is its own
          physical file row number (``__row1__`` / ``__row2__``) — matching
          native's ``__source_row___file1`` / ``__source_row___file2``.
        - *physical_source_rows=False* (the header re-read regime — no
          ``__source_row__``): both sides' source row is the merged-frame
          1-based *position* (``idx + 1`` over the ordered join) — matching
          native's positional fallback.

        Args:
            con: Open DuckDB connection with ``f1`` / ``f2`` available.  Each
                table/view exposes the data columns plus a 1-based ``__row__``
                ordering column.
            columns: Ordered column names (header order).
            key_columns: Key column names.
            detailed: Include field-level analysis and ``field_statistics``.
            coalesce: When True (native-read path) wrap every value column in
                ``COALESCE(col,'')`` so DuckDB NULLs (empty fields) match pandas'
                ``keep_default_na=False`` empty strings.  When False (fallback)
                the registered frames already hold ``''`` so no coalescing.
            physical_source_rows: Source-row convention selector (see above).
            total1: Pre-computed file1 row count (native-read path), else
                computed here.
            total2: Pre-computed file2 row count (native-read path), else
                computed here.

        Returns:
            The native in-memory comparison result dict.
        """
        value_columns = [c for c in columns if c not in key_columns]

        def q(identifier: str) -> str:
            """Quote a SQL identifier (doubling embedded quotes)."""
            return '"' + identifier.replace('"', '""') + '"'

        def val(alias: str, col: str) -> str:
            """Render a value expression, coalescing NULL→'' when required."""
            ref = f"{alias}.{q(col)}"
            return f"COALESCE({ref}, '')" if coalesce else ref

        if total1 is None:
            total1 = con.execute("SELECT COUNT(*) FROM f1").fetchone()[0]
        if total2 is None:
            total2 = con.execute("SELECT COUNT(*) FROM f2").fetchone()[0]

        # --- Matched rows: SQL-FILTERED to the DIFFERING rows only -------------
        # The perf-critical move (S25-3): the non-differing matched rows (usually
        # the vast majority) are *counted* set-based in SQL and NEVER fetched into
        # Python.  Only rows where at least one value column differs are pulled
        # into the materialization loop.  A window ``row_number()`` over the
        # pandas-merge ordering ``(f1.__row__, f2.__row__)`` carries each
        # differing row's full-merge 1-based position, so the header-regime
        # source_row (the merged-frame position) is preserved even though the
        # intervening non-differing rows were filtered out.
        select_parts = [f"{val('f1', c)} AS {q(c)}" for c in key_columns]
        for col in value_columns:
            select_parts.append(f"{val('f1', col)} AS {q(col + '__1')}")
            select_parts.append(f"{val('f2', col)} AS {q(col + '__2')}")
        select_parts.append("f1.__row__ AS __row1__")
        select_parts.append("f2.__row__ AS __row2__")
        select_parts.append(
            "row_number() OVER (ORDER BY f1.__row__, f2.__row__) AS __pos__"
        )

        join_on = " AND ".join(f"f1.{q(c)} = f2.{q(c)}" for c in key_columns)
        if value_columns:
            # The predicate runs in the OUTER query over the CTE's already-aliased
            # / already-coalesced ``col__1`` / ``col__2`` columns (so f1/f2 are
            # out of scope here, and the COALESCE — when applied — is reused).
            diff_predicate = " OR ".join(
                f"{q(c + '__1')} <> {q(c + '__2')}" for c in value_columns
            )
        else:
            # No value columns → nothing can differ; the join exists only to
            # count matches.  ``FALSE`` filters all rows out of the diff fetch.
            diff_predicate = "FALSE"

        diff_sql = (
            f"WITH j AS (SELECT {', '.join(select_parts)} "
            f"FROM f1 JOIN f2 ON {join_on}) "
            f"SELECT * FROM j WHERE {diff_predicate} ORDER BY __pos__"
        )
        cursor = con.execute(diff_sql)
        matched = cursor.fetchall()
        matched_cols = [d[0] for d in cursor.description]
        col_idx = {name: i for i, name in enumerate(matched_cols)}

        differences: list[dict[str, Any]] = []
        for row in matched:
            row_diffs: dict[str, Any] = {}
            for col in value_columns:
                v1 = row[col_idx[col + "__1"]]
                v2 = row[col_idx[col + "__2"]]
                v1 = "" if v1 is None else v1
                v2 = "" if v2 is None else v2
                if v1 != v2:
                    if detailed:
                        row_diffs[col] = self._analyze_field_difference(v1, v2)
                    else:
                        row_diffs[col] = {"file1": v1, "file2": v2}
            # row_diffs is always non-empty here (SQL pre-filtered to differing
            # rows), but guard defensively against COALESCE/compare edge skew.
            if row_diffs:
                key_values = {k: row[col_idx[k]] for k in key_columns}
                entry: dict[str, Any] = {
                    "keys": key_values,
                    "differences": row_diffs,
                }
                if detailed:
                    entry["difference_count"] = len(row_diffs)
                if physical_source_rows:
                    entry["source_row_file1"] = int(row[col_idx["__row1__"]])
                    entry["source_row_file2"] = int(row[col_idx["__row2__"]])
                else:
                    entry["source_row_file1"] = int(row[col_idx["__pos__"]])
                    entry["source_row_file2"] = int(row[col_idx["__pos__"]])
                differences.append(entry)

        # --- only_in via anti-join, full rows as DataFrames ------------------
        only_in_file1 = self._fetch_only_in(
            con, "f1", "f2", columns, key_columns, q, coalesce
        )
        only_in_file2 = self._fetch_only_in(
            con, "f2", "f1", columns, key_columns, q, coalesce
        )

        total_differences = len(differences)
        field_stats = (
            self._calculate_field_statistics(differences) if detailed else {}
        )

        return {
            "only_in_file1": only_in_file1,
            "only_in_file2": only_in_file2,
            "differences": differences,
            "total_rows_file1": total1,
            "total_rows_file2": total2,
            "matching_rows": total1 - len(only_in_file1) - total_differences,
            "rows_with_differences": total_differences,
            "field_statistics": field_stats,
            "structure_compatible": True,
        }

    @staticmethod
    def _fetch_only_in(
        con: Any,
        source: str,
        other: str,
        columns: list[str],
        key_columns: list[str],
        q: Any,
        coalesce: bool,
    ) -> pd.DataFrame:
        """Return rows whose key is in *source* but not *other* as a DataFrame.

        Reproduces the native ``only_in_file*`` payload: a pandas DataFrame of the
        **full source rows** (all columns, header order, values as text).  The
        anti-join predicate compares keys with the same NULL→'' coalescing the
        diff uses so a key whose value is empty is matched consistently.

        Args:
            con: Open DuckDB connection.
            source: Table whose unique rows are returned.
            other: Reference table.
            columns: Ordered column names to project (source header order).
            key_columns: Key columns for the anti-join predicate.
            q: Identifier-quoting callable.
            coalesce: Apply NULL→'' coalescing to projected/compared values.

        Returns:
            DataFrame of the unique source rows (may be empty).
        """
        def projected(col: str) -> str:
            ref = f"s.{q(col)}"
            return (f"COALESCE({ref}, '')" if coalesce else ref) + f" AS {q(col)}"

        def key_eq(col: str) -> str:
            s_ref, o_ref = f"s.{q(col)}", f"o.{q(col)}"
            if coalesce:
                return f"COALESCE({s_ref}, '') = COALESCE({o_ref}, '')"
            return f"{s_ref} = {o_ref}"

        select_cols = ", ".join(projected(c) for c in columns)
        not_exists = " AND ".join(key_eq(c) for c in key_columns)
        sql = (
            f"SELECT {select_cols} FROM {source} s "
            f"WHERE NOT EXISTS (SELECT 1 FROM {other} o WHERE {not_exists})"
        )
        df = con.execute(sql).fetchdf()
        if not df.empty:
            df = df.fillna("")
            for c in df.columns:
                df[c] = df[c].astype(str)
        else:
            df = pd.DataFrame(columns=columns)
        return df[columns]

    @staticmethod
    def _structure_incompatible_result(
        df1: pd.DataFrame,
        df2: pd.DataFrame,
        structure_errors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build native's structure-incompatible early-return dict."""
        return {
            "structure_compatible": False,
            "structure_errors": structure_errors,
            "total_rows_file1": len(df1),
            "total_rows_file2": len(df2),
            "matching_rows": 0,
            "only_in_file1": 0,
            "only_in_file2": 0,
            "differences": 0,
        }

    # ------------------------------------------------------------------
    # Field analysis (mirrors FileComparator semantics for text values)
    # ------------------------------------------------------------------
    @staticmethod
    def _analyze_field_difference(val1: str, val2: str) -> dict[str, Any]:
        """Build the native detailed-diff dict for two text field values.

        Args:
            val1: File1 value (text).
            val2: File2 value (text).

        Returns:
            Dict with ``file1``, ``file2``, ``type`` and ``string_analysis``
            matching :meth:`FileComparator._analyze_field_difference`.
        """
        return {
            "file1": val1,
            "file2": val2,
            "type": "value_difference",
            "string_analysis": {
                "length_diff": len(val2) - len(val1),
                "case_only": val1.lower() == val2.lower(),
                "whitespace_diff": val1.strip() == val2.strip(),
                "length_file1": len(val1),
                "length_file2": len(val2),
            },
        }

    @staticmethod
    def _calculate_field_statistics(
        differences: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Compute the native ``field_statistics`` dict from the diff list.

        Args:
            differences: The materialized differences list.

        Returns:
            The native field-statistics dict.
        """
        fc = FileComparator.__new__(FileComparator)
        return fc._calculate_field_statistics(differences)
