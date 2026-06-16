"""Truth-source backend abstraction for the L2b SQL-truth reconciliation.

The L2b SQL-truth gate reconciles a Valdo output file against an expected
rowset derived from a database "truth source". Historically the only truth
source was Oracle, and the connection was opened by hard-wired ``oracledb``
calls inside the E2E orchestrator
(:func:`scripts.e2e_lib.run_source._open_l2b_connection`).

This module introduces a small interface — :class:`TruthSource` — so that
Oracle becomes *one* implementation behind a stable seam. A future backend
(PostgreSQL, a flat-file/Parquet "expected" extract, …) can be added as a new
adapter without touching the orchestrator or the comparator engine.

This is the first slice (ADR 0010 / R-01a): the interface and the
:class:`OracleTruthSource` adapter land here, but no caller is rewired yet
(that is R-01b). The reconciliation engine already accepts a PEP-249
connection, so adapters need only *produce* one.

Design notes
------------
* The interface is intentionally tiny: ``connect()`` returns a PEP-249
  connection, ``close()`` releases it, and :attr:`TruthSource.dialect`
  exposes a hint object so the engine/spec can adapt SQL-dialect concerns in
  a later slice (R-01c) without a behaviour change today.
* :class:`OracleTruthSource` mirrors the *exact* credential convention of the
  pre-existing opener: ``ORACLE_DSN`` / ``ORACLE_USER`` / ``ORACLE_PASSWORD``
  resolved through the same secret-resolver protocol. It performs no logging
  and raises a single :class:`TruthSourceError` on failure, leaving the
  degrade-to-``infra_error`` policy to the caller (preserved in R-01b).
* No secret values are ever included in error messages (AGENTS.md #3).

Relationship to the ``DatabaseAdapter`` factory (ADR 0022 §6)
-------------------------------------------------------------
This module is **intentionally separate** from
:func:`src.database.adapters.factory.get_database_adapter` and is **not** dead
code. The two seams serve *different consumers* with *different contracts*:

* :class:`TruthSource` returns a **raw PEP-249 connection** for the L2b /
  E2E-harness comparator engine (``scripts/e2e_lib/run_source.py`` →
  :func:`scripts.e2e_lib.run_source._open_l2b_connection`, and
  ``scripts/e2e_lib/db_truth_comparator.py``). The comparator engine drives the
  cursor itself.
* :class:`~src.database.adapters.base.DatabaseAdapter` returns a higher-level
  pandas/file API (``execute_query`` / ``extract_to_file`` / ``get_column_metadata``)
  for the in-process ``src/`` DB-integration features
  (``reconcile`` / ``extract`` / ``db-compare``).

ADR 0010 deliberately scoped ``TruthSource`` to "produce a connection" for the
comparator, and ADR 0022 explicitly **declines to merge** the two seams. Unifying
:class:`SqlDialect` (here) with
:class:`~src.database.adapters.types.CanonicalType` is deferred until a second,
non-Oracle ``TruthSource`` backend actually lands (the same deferral ADR 0010
made for its own dialect shape) — do not build it speculatively.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Optional, Protocol


class TruthSourceError(RuntimeError):
    """Raised when a truth source cannot establish or release a connection.

    The message never includes secret values — only the *name* of a missing
    secret or a driver-supplied error string.
    """


class _SecretLookup(Protocol):
    """Minimal protocol for the secret accessor a truth source needs.

    Matches :meth:`scripts.e2e_lib.secret_resolver.SecretResolver.get`. Kept as
    a structural type so this module does not import the harness (avoiding a
    ``src/`` → ``scripts/`` dependency direction).
    """

    def get(self, name: str) -> str:  # pragma: no cover - structural typing
        ...


@dataclass(frozen=True)
class SqlDialect:
    """A small, declarative hint describing a backend's SQL dialect.

    This is a placeholder seam for R-01c. Today only :attr:`name` is consumed
    (informationally); future fields (identifier quoting style, etc.) are added
    here so the comparator/spec can adapt without per-backend branches.

    Attributes:
        name: Short dialect identifier, e.g. ``"oracle"``.
    """

    name: str


# Sentinel dialects. Add new ones alongside their adapter.
ORACLE_DIALECT = SqlDialect(name="oracle")


class TruthSource(abc.ABC):
    """A source of expected rows for L2b reconciliation.

    Implementations open a PEP-249 connection that the reconciliation engine
    (:func:`scripts.e2e_lib.db_truth_comparator.reconcile`) drives. The engine
    performs no commits or rollbacks; the truth source owns the connection
    lifecycle via :meth:`connect` / :meth:`close`.
    """

    @property
    @abc.abstractmethod
    def dialect(self) -> SqlDialect:
        """Return the SQL dialect hint for this backend."""
        raise NotImplementedError

    @abc.abstractmethod
    def connect(self) -> Any:
        """Open and return a PEP-249 connection.

        Returns:
            A live PEP-249 connection object.

        Raises:
            TruthSourceError: If credentials cannot be resolved or the driver
                fails to connect. No secret value appears in the message.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def close(self) -> None:
        """Release the connection if one is open. Idempotent and best-effort."""
        raise NotImplementedError

    def __enter__(self) -> Any:
        return self.connect()

    def __exit__(self, *_exc: Any) -> None:
        self.close()


class OracleTruthSource(TruthSource):
    """Oracle implementation of :class:`TruthSource`.

    Mirrors the credential convention of the legacy
    ``run_source._open_l2b_connection``: ``ORACLE_DSN`` / ``ORACLE_USER`` /
    ``ORACLE_PASSWORD`` resolved via the injected secret accessor. The
    ``oracledb`` driver is imported lazily so importing this module never
    requires the driver (it is absent on dev boxes / CI).

    Args:
        secret_lookup: An object exposing ``get(name) -> str`` (e.g.
            ``SecretResolver``). Required.
        connect_fn: Optional injection point for tests — a callable
            ``(user, password, dsn) -> connection``. Defaults to
            ``oracledb.connect`` resolved lazily at :meth:`connect` time.
        dsn_secret / user_secret / password_secret: Secret *names* to resolve.
            Defaults match the legacy opener.
    """

    def __init__(
        self,
        *,
        secret_lookup: _SecretLookup,
        connect_fn: Optional[Any] = None,
        dsn_secret: str = "ORACLE_DSN",
        user_secret: str = "ORACLE_USER",
        password_secret: str = "ORACLE_PASSWORD",
    ) -> None:
        self._secret_lookup = secret_lookup
        self._connect_fn = connect_fn
        self._dsn_secret = dsn_secret
        self._user_secret = user_secret
        self._password_secret = password_secret
        self._conn: Optional[Any] = None

    @property
    def dialect(self) -> SqlDialect:
        return ORACLE_DIALECT

    def connect(self) -> Any:
        # Resolve credentials first; surface a missing-secret name (never value).
        try:
            dsn = self._secret_lookup.get(self._dsn_secret)
            user = self._secret_lookup.get(self._user_secret)
            password = self._secret_lookup.get(self._password_secret)
        except Exception as exc:  # noqa: BLE001 — preserve resolver wording
            raise TruthSourceError(
                f"could not resolve Oracle credentials: {exc}"
            ) from exc

        connect_fn = self._connect_fn
        if connect_fn is None:
            try:
                import oracledb  # type: ignore
            except ImportError as exc:  # pragma: no cover — RHEL-only path
                raise TruthSourceError("oracledb driver is not installed") from exc
            connect_fn = oracledb.connect

        try:
            self._conn = connect_fn(user=user, password=password, dsn=dsn)
        except Exception as exc:  # noqa: BLE001 — driver decides the type
            raise TruthSourceError(f"Oracle connection failed: {exc}") from exc
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 — best effort on teardown
                pass
            finally:
                self._conn = None
