"""Failure sink: append-only writer for ``AUDIT.VALDO_RUN_FAILURES``.

Purpose
-------
Every wrapper script in the E2E batch testing harness funnels gate failures
through this module so the on-call team has a single Oracle table to query
when a nightly run goes red. Implements gap **(a)** from the E2E prompt.

Design
------
* **Append-only**. The public API exposes ``insert_failure`` and
  ``insert_failures`` only. There is no update / delete / upsert path on
  purpose — the audit trail must be preservable verbatim.
* **DB-agnostic via DB-API 2.0**. The default production binding is
  ``python-oracledb`` (Oracle thin mode), but the writer accepts any
  PEP-249 connection so the integration tests can substitute SQLite.
* **No secret leakage**. Connection parameters are resolved through
  :class:`scripts.e2e_lib.secret_resolver.SecretResolver`; nothing in this
  module calls ``os.environ`` directly. Errors mention secret *names*, never
  values.
* **Idempotent at the row level**. A unique ``run_id + gate_name + layer +
  file_name + error_type`` tuple per call is the caller's responsibility;
  this module performs straight INSERTs. (The DDL deliberately does not
  declare a unique constraint so retries do not poison the audit trail with
  ORA-00001 — duplicates are acceptable and traceable via ``failed_at``.)

Production wiring
-----------------
::

    from scripts.e2e_lib.failure_sink import FailureSink, FailureRecord
    from scripts.e2e_lib.secret_resolver import SecretResolver

    sink = FailureSink.for_oracle(
        env="sit",
        resolver=SecretResolver.default(),
        table="AUDIT.VALDO_RUN_FAILURES",
    )
    sink.insert_failure(FailureRecord(
        run_id="20260513_120000",
        env="sit",
        source="SRC_A",
        pipeline_name="SRC_A.pipeline",
        gate_name="L1_structural",
        gate_stage="output",
        layer="L1",
        file_name="SRC_A_P327_20260513.txt",
        file_type="P327",
        mapping_path="config/mappings/SRC_A_P327.json",
        mapping_version="1.4.0",
        error_type="structural",
        error_count=3,
        row_count=10_482,
        report_path="/data/sit/_reports/20260513_120000/SRC_A/P327.html",
        blocking=True,
        failure_detail={"first_error": "record 17: width mismatch"},
    ))
    sink.close()

Testing
-------
``FailureSink.from_connection(conn, table=...)`` accepts a pre-built
DB-API 2.0 connection. The integration test in
``tests/integration/test_e2e_failure_sink.py`` uses a SQLite in-memory
connection with a SQLite-compatible mirror of the Oracle DDL.
"""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterable, List, Mapping, Optional, Sequence

from scripts.e2e_lib.secret_resolver import SecretResolver

DEFAULT_TABLE = "AUDIT.VALDO_RUN_FAILURES"

# Columns written by this module, in the canonical order used for INSERTs.
# ``failure_id`` is identity / autoincrement, so it is intentionally omitted.
# ``failed_at`` is omitted when the caller passes ``None`` so the database
# default (``SYSTIMESTAMP``) fires; otherwise we pass the caller's value.
_WRITABLE_COLUMNS: Sequence[str] = (
    "run_id",
    "env",
    "source",
    "pipeline_name",
    "gate_name",
    "gate_stage",
    "layer",
    "file_name",
    "file_type",
    "mapping_path",
    "mapping_version",
    "error_type",
    "error_count",
    "row_count",
    "report_path",
    "blocking",
    "failed_at",
    "failure_detail",
)

_VALID_ENVS = {"sit", "ait"}
# L2 (regeneration) was retired (ADR 0012); L2b is the live SQL-truth layer.
_VALID_LAYERS = {None, "L1", "L2b", "L3"}


class FailureSinkError(RuntimeError):
    """Raised when the failure sink cannot persist a record."""


@dataclass
class FailureRecord:
    """One row destined for ``AUDIT.VALDO_RUN_FAILURES``.

    Mirrors the DDL in ``scripts/sql/audit_valdo_run_failures.sql`` with a
    couple of ergonomic conversions handled at write time:

    * ``blocking`` is a Python ``bool``; persisted as ``1`` / ``0``.
    * ``failure_detail`` accepts a dict, list, or string. Dicts/lists are
      JSON-serialized; strings are passed through unchanged.
    * ``failed_at`` is optional; when ``None`` the DB default fires.
    """

    run_id: str
    env: str
    source: str
    pipeline_name: str
    gate_name: str
    error_type: str
    gate_stage: Optional[str] = None
    layer: Optional[str] = None
    file_name: Optional[str] = None
    file_type: Optional[str] = None
    mapping_path: Optional[str] = None
    mapping_version: Optional[str] = None
    error_count: int = 0
    row_count: Optional[int] = None
    report_path: Optional[str] = None
    blocking: bool = True
    failed_at: Optional[datetime] = None
    failure_detail: Any = field(default=None)

    def __post_init__(self) -> None:
        _require_nonempty("run_id", self.run_id)
        _require_nonempty("env", self.env)
        _require_nonempty("source", self.source)
        _require_nonempty("pipeline_name", self.pipeline_name)
        _require_nonempty("gate_name", self.gate_name)
        _require_nonempty("error_type", self.error_type)
        if self.env not in _VALID_ENVS:
            raise FailureSinkError(
                f"env must be one of {sorted(_VALID_ENVS)}, got {self.env!r}"
            )
        if self.layer not in _VALID_LAYERS:
            raise FailureSinkError(
                f"layer must be one of {sorted(str(x) for x in _VALID_LAYERS)}, "
                f"got {self.layer!r}"
            )
        if self.error_count is None or self.error_count < 0:
            raise FailureSinkError("error_count must be a non-negative integer")
        if self.row_count is not None and self.row_count < 0:
            raise FailureSinkError("row_count must be >= 0 when provided")

    def to_row(self) -> "Mapping[str, Any]":
        """Return a column-name → value mapping ready for INSERT binding."""
        detail = self.failure_detail
        if detail is not None and not isinstance(detail, str):
            try:
                detail = json.dumps(detail, default=str, sort_keys=True)
            except (TypeError, ValueError) as exc:
                raise FailureSinkError(
                    f"failure_detail is not JSON-serializable: {exc}"
                ) from exc
        return {
            "run_id": self.run_id,
            "env": self.env,
            "source": self.source,
            "pipeline_name": self.pipeline_name,
            "gate_name": self.gate_name,
            "gate_stage": self.gate_stage,
            "layer": self.layer,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "mapping_path": self.mapping_path,
            "mapping_version": self.mapping_version,
            "error_type": self.error_type,
            "error_count": int(self.error_count),
            "row_count": self.row_count,
            "report_path": self.report_path,
            "blocking": 1 if self.blocking else 0,
            "failed_at": self.failed_at,
            "failure_detail": detail,
        }

    def to_dict(self) -> "Mapping[str, Any]":
        """Plain dict view, useful for logging without secrets."""
        return asdict(self)


def _require_nonempty(label: str, value: Optional[str]) -> None:
    if value is None or str(value).strip() == "":
        raise FailureSinkError(f"{label} must be a non-empty string")


# --------------------------------------------------------------------------- #
# Sink
# --------------------------------------------------------------------------- #


@dataclass
class FailureSink:
    """Writer for ``AUDIT.VALDO_RUN_FAILURES``.

    Construct via one of the factory classmethods:

    * :meth:`for_oracle` — production: opens an Oracle connection via
      ``python-oracledb`` using secrets resolved through
      :class:`SecretResolver`.
    * :meth:`from_connection` — tests: takes a pre-built DB-API 2.0
      connection so SQLite can stand in for Oracle.
    """

    connection: Any
    table: str = DEFAULT_TABLE
    paramstyle: str = "named"  # "named" → :name, "qmark" → ?

    # ------------------------- factories ------------------------- #

    @classmethod
    def for_oracle(
        cls,
        env: str,
        resolver: Optional[SecretResolver] = None,
        *,
        dsn_env: Optional[str] = None,
        user_env: Optional[str] = None,
        password_env: Optional[str] = None,
        table: str = DEFAULT_TABLE,
    ) -> "FailureSink":
        """Open an Oracle connection and return a sink bound to it.

        Args:
            env: Either ``"sit"`` or ``"ait"``. Used for validation and for
                error messages; the actual credential lookup uses the env-var
                *names* given by ``dsn_env`` / ``user_env`` / ``password_env``.
            resolver: Secret resolver. Defaults to ``SecretResolver.default()``.
            dsn_env: Env-var name for the DSN. Default: ``ORACLE_DSN``
                (matches the rest of Valdo and the ``.env`` convention).
                Pass a per-env name like ``ORACLE_DSN_SIT`` when sites adopt
                the suffixed convention; this is usually wired by the wrapper
                script from ``config/e2e/paths.yml`` (``oracle_dsn_env`` key).
            user_env: Env-var name for the username. Default: ``ORACLE_USER``.
            password_env: Env-var name for the password.
                Default: ``ORACLE_PASSWORD``.
            table: Fully-qualified target table.

        Raises:
            FailureSinkError: On unsupported env or missing secrets.
        """
        if env not in _VALID_ENVS:
            raise FailureSinkError(
                f"env must be one of {sorted(_VALID_ENVS)}, got {env!r}"
            )
        resolver = resolver or SecretResolver.default()
        # Defaults match Valdo's existing .env convention (bare names).
        # Callers can override via paths.yml -> oracle_*_env when adopting
        # per-env suffixed names like ORACLE_DSN_SIT.
        dsn_key = dsn_env or "ORACLE_DSN"
        user_key = user_env or "ORACLE_USER"
        pw_key = password_env or "ORACLE_PASSWORD"

        try:
            dsn = resolver.get(dsn_key)
            user = resolver.get(user_key)
            password = resolver.get(pw_key)
        except Exception as exc:  # noqa: BLE001 — preserve resolver wording
            raise FailureSinkError(
                f"unable to resolve Oracle credentials for env={env!r}: {exc}"
            ) from exc

        try:
            import oracledb  # type: ignore
        except ImportError as exc:  # pragma: no cover — exercised on RHEL only
            raise FailureSinkError(
                "python-oracledb is required for FailureSink.for_oracle"
            ) from exc

        try:
            conn = oracledb.connect(user=user, password=password, dsn=dsn)
        except Exception as exc:  # noqa: BLE001
            raise FailureSinkError(
                f"failed to connect to Oracle dsn={dsn_key} as user={user_key}: {exc}"
            ) from exc

        return cls(connection=conn, table=table, paramstyle="named")

    @classmethod
    def from_connection(
        cls,
        connection: Any,
        *,
        table: str = DEFAULT_TABLE,
        paramstyle: str = "named",
    ) -> "FailureSink":
        """Bind to a pre-built DB-API 2.0 connection (used in tests)."""
        if paramstyle not in {"named", "qmark"}:
            raise FailureSinkError(
                f"paramstyle must be 'named' or 'qmark', got {paramstyle!r}"
            )
        return cls(connection=connection, table=table, paramstyle=paramstyle)

    # ------------------------- writes ------------------------- #

    def insert_failure(self, record: FailureRecord) -> None:
        """Insert a single failure row and commit."""
        self.insert_failures([record])

    def insert_failures(self, records: Iterable[FailureRecord]) -> int:
        """Insert many rows in one transaction. Returns the row count.

        The whole batch is committed atomically: either every row lands
        or none do. This protects the constraint in the prompt
        ("never half-write AUDIT.VALDO_RUN_FAILURES").
        """
        rows: List[FailureRecord] = list(records)
        if not rows:
            return 0

        sql, bindings = self._build_insert(rows)

        try:
            with closing(self.connection.cursor()) as cursor:
                if self.paramstyle == "named":
                    cursor.executemany(sql, bindings)
                else:
                    # qmark: bindings is a list of tuples in column order
                    cursor.executemany(sql, bindings)
            self.connection.commit()
        except Exception as exc:  # noqa: BLE001 — keep details out of stdout
            try:
                self.connection.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise FailureSinkError(
                f"failed to insert {len(rows)} row(s) into {self.table}: {exc}"
            ) from exc
        return len(rows)

    def _build_insert(
        self, records: Sequence[FailureRecord]
    ) -> "tuple[str, list]":
        """Return ``(sql, bindings)`` for an executemany call.

        ``failed_at`` is omitted from the INSERT when ALL records leave it
        as ``None``, so that the DB default ``SYSTIMESTAMP`` fires. If any
        record sets it, every record must contribute a value (we substitute
        ``None`` → SQL NULL for the absent ones, which is consistent with
        the column being nullable from the writer's perspective). Mixing
        within a single batch is supported.
        """
        any_failed_at = any(r.failed_at is not None for r in records)
        columns = [c for c in _WRITABLE_COLUMNS if c != "failed_at" or any_failed_at]

        if self.paramstyle == "named":
            placeholders = ", ".join(f":{c}" for c in columns)
            sql = (
                f"INSERT INTO {self.table} ({', '.join(columns)}) "
                f"VALUES ({placeholders})"
            )
            bindings = [
                {c: r.to_row()[c] for c in columns} for r in records
            ]
        else:
            placeholders = ", ".join("?" for _ in columns)
            sql = (
                f"INSERT INTO {self.table} ({', '.join(columns)}) "
                f"VALUES ({placeholders})"
            )
            bindings = [
                tuple(r.to_row()[c] for c in columns) for r in records
            ]
        return sql, bindings

    # ------------------------- lifecycle ------------------------- #

    def close(self) -> None:
        """Close the underlying connection. Safe to call multiple times."""
        conn = self.connection
        if conn is None:
            return
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — closing is best-effort
            pass
        finally:
            self.connection = None

    def __enter__(self) -> "FailureSink":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
