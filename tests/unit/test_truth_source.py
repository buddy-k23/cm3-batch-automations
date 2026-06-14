"""Unit tests for the TruthSource backend abstraction (ADR 0010 / R-01a).

These tests pin the interface contract and the OracleTruthSource adapter
behaviour WITHOUT requiring the ``oracledb`` driver or a live database: the
adapter accepts an injected ``connect_fn`` and a fake secret lookup.
"""

from __future__ import annotations

import pytest

from src.database.truth_source import (
    ORACLE_DIALECT,
    OracleTruthSource,
    SqlDialect,
    TruthSource,
    TruthSourceError,
)


class _FakeSecrets:
    """Minimal secret accessor matching the _SecretLookup protocol."""

    def __init__(self, values):
        self._values = values

    def get(self, name: str) -> str:
        if name not in self._values:
            raise KeyError(f"secret '{name}' is not set")
        return self._values[name]


class _FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _oracle_secrets():
    return _FakeSecrets(
        {
            "ORACLE_DSN": "host:1521/svc",
            "ORACLE_USER": "app_int",
            "ORACLE_PASSWORD": "s3cret",
        }
    )


# --------------------------------------------------------------------------- #
# Interface contract
# --------------------------------------------------------------------------- #


def test_truthsource_is_abstract():
    with pytest.raises(TypeError):
        TruthSource()  # type: ignore[abstract]


def test_custom_truthsource_implementation_satisfies_contract():
    """A fake in-memory TruthSource can be implemented from the ABC alone."""

    class FakeTruthSource(TruthSource):
        def __init__(self):
            self._conn = None

        @property
        def dialect(self) -> SqlDialect:
            return SqlDialect(name="fake")

        def connect(self):
            self._conn = _FakeConn()
            return self._conn

        def close(self) -> None:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    ts = FakeTruthSource()
    assert ts.dialect.name == "fake"
    conn = ts.connect()
    assert isinstance(conn, _FakeConn)
    ts.close()
    assert conn.closed is True


def test_truthsource_context_manager():
    created = {}

    class FakeTruthSource(TruthSource):
        @property
        def dialect(self):
            return SqlDialect(name="fake")

        def connect(self):
            created["conn"] = _FakeConn()
            return created["conn"]

        def close(self):
            created["conn"].close()

    with FakeTruthSource() as conn:
        assert isinstance(conn, _FakeConn)
    assert created["conn"].closed is True


# --------------------------------------------------------------------------- #
# OracleTruthSource adapter
# --------------------------------------------------------------------------- #


def test_oracle_truth_source_dialect():
    ts = OracleTruthSource(secret_lookup=_oracle_secrets())
    assert ts.dialect is ORACLE_DIALECT
    assert ts.dialect.name == "oracle"


def test_oracle_connect_uses_resolved_credentials():
    captured = {}

    def fake_connect(*, user, password, dsn):
        captured.update(user=user, password=password, dsn=dsn)
        return _FakeConn()

    ts = OracleTruthSource(secret_lookup=_oracle_secrets(), connect_fn=fake_connect)
    conn = ts.connect()
    assert isinstance(conn, _FakeConn)
    assert captured == {
        "user": "app_int",
        "password": "s3cret",
        "dsn": "host:1521/svc",
    }


def test_oracle_close_is_idempotent():
    conn = _FakeConn()
    ts = OracleTruthSource(secret_lookup=_oracle_secrets(), connect_fn=lambda **_: conn)
    ts.connect()
    ts.close()
    ts.close()  # second call must not raise
    assert conn.closed is True


def test_oracle_missing_secret_raises_truthsource_error_without_value():
    secrets = _FakeSecrets({"ORACLE_DSN": "d", "ORACLE_USER": "u"})  # no password
    ts = OracleTruthSource(secret_lookup=secrets, connect_fn=lambda **_: _FakeConn())
    with pytest.raises(TruthSourceError) as exc:
        ts.connect()
    msg = str(exc.value)
    assert "could not resolve Oracle credentials" in msg
    # The secret value must never leak; only its name / the resolver wording.
    assert "s3cret" not in msg


def test_oracle_connect_failure_wrapped():
    def boom(**_):
        raise RuntimeError("ORA-12154: TNS could not resolve")

    ts = OracleTruthSource(secret_lookup=_oracle_secrets(), connect_fn=boom)
    with pytest.raises(TruthSourceError) as exc:
        ts.connect()
    assert "Oracle connection failed" in str(exc.value)
