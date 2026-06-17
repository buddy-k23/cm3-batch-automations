"""Unit tests for db_health_service connectivity probe (S17-4, #432).

The probe must be non-throwing and bounded: any adapter failure or timeout
resolves to ``False`` rather than propagating.
"""

import time
from unittest import mock

from src.services import db_health_service


def _adapter(success=True, raise_on=None):
    adapter = mock.Mock()
    if raise_on == "connect":
        adapter.connect.side_effect = RuntimeError("no connect")
    if raise_on == "query":
        adapter.execute_query.side_effect = RuntimeError("no query")
    if success and raise_on is None:
        adapter.execute_query.return_value = [(1,)]
    return adapter


def test_probe_success_returns_true():
    adapter = _adapter(success=True)
    with mock.patch(
        "src.database.adapters.factory.get_database_adapter", return_value=adapter
    ):
        assert db_health_service.check_db_connectivity() is True
    adapter.connect.assert_called_once()
    adapter.disconnect.assert_called_once()


def test_probe_connect_failure_returns_false():
    adapter = _adapter(raise_on="connect")
    with mock.patch(
        "src.database.adapters.factory.get_database_adapter", return_value=adapter
    ):
        assert db_health_service.check_db_connectivity() is False


def test_probe_query_failure_disconnects_and_returns_false():
    adapter = _adapter(raise_on="query")
    with mock.patch(
        "src.database.adapters.factory.get_database_adapter", return_value=adapter
    ):
        assert db_health_service.check_db_connectivity() is False
    # disconnect runs in the finally even though the query raised
    adapter.disconnect.assert_called_once()


def test_factory_failure_returns_false():
    with mock.patch(
        "src.database.adapters.factory.get_database_adapter",
        side_effect=ImportError("driver missing"),
    ):
        assert db_health_service.check_db_connectivity() is False


def test_timeout_returns_false():
    def slow_adapter(*_a, **_k):
        adapter = mock.Mock()

        def slow_connect():
            time.sleep(1.0)

        adapter.connect.side_effect = slow_connect
        return adapter

    with mock.patch(
        "src.database.adapters.factory.get_database_adapter",
        side_effect=slow_adapter,
    ):
        assert db_health_service.check_db_connectivity(timeout_seconds=0.05) is False
