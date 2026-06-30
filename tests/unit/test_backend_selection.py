"""Backend selection + opt-in wiring tests (S25-4).

Pins the three ``COMPARISON_BACKEND`` selection modes and the opt-in wiring that
lets a caller pass an explicit backend to
:func:`src.services.compare_service.run_compare_service`:

- ``native`` / ``pandas`` — always the default in-memory engine (and the value
  used when the env var is unset, so the default path is byte-identical to the
  historical behaviour).
- ``duckdb`` — always the DuckDB backend (raises the clear install error if the
  optional ``duckdb`` package is absent; that error path is owned by the backend
  and covered in ``test_duckdb_backend.py``).
- ``auto`` — DuckDB **only when** ``duckdb`` is importable AND the comparison is
  in the large regime (a file ``>= CHUNK_THRESHOLD_BYTES`` /
  :func:`should_use_chunked`); otherwise native.  When ``duckdb`` is absent,
  ``auto`` silently uses native (no error, no import of duckdb).

The ``auto``-without-duckdb path is proven by mocking
``importlib.util.find_spec`` to return ``None`` — duckdb is *not* uninstalled —
so the test is valid regardless of whether the package is present.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.comparators.backends.factory import (
    get_comparison_backend,
    resolve_backend,
)
from src.comparators.backends.native_backend import NativeComparisonBackend
from src.services.compare_service import (
    CHUNK_THRESHOLD_BYTES,
    run_compare_service,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------
HEADER = "customer_id|name|balance"


def _write(path: Path, rows: list[str]) -> str:
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


@pytest.fixture()
def small_files(tmp_path):
    """Two small pipe-delimited files well under the 50 MB regime threshold."""
    f1 = _write(tmp_path / "a.txt", [HEADER, "1|alice|100", "2|bob|200", "3|carol|300"])
    f2 = _write(tmp_path / "b.txt", [HEADER, "1|alice|100", "2|bob|999", "3|carol|300"])
    return f1, f2


# ---------------------------------------------------------------------------
# resolve_backend: explicit > env > default; native default
# ---------------------------------------------------------------------------
def test_resolve_default_is_native(monkeypatch, small_files):
    """Env unset, no explicit name -> 'native'."""
    monkeypatch.delenv("COMPARISON_BACKEND", raising=False)
    f1, f2 = small_files
    assert resolve_backend(f1, f2) == "native"


@pytest.mark.parametrize("value", ["native", "pandas"])
def test_resolve_env_native_aliases(monkeypatch, small_files, value):
    monkeypatch.setenv("COMPARISON_BACKEND", value)
    f1, f2 = small_files
    assert resolve_backend(f1, f2) == value


def test_resolve_env_duckdb(monkeypatch, small_files):
    monkeypatch.setenv("COMPARISON_BACKEND", "duckdb")
    f1, f2 = small_files
    assert resolve_backend(f1, f2) == "duckdb"


def test_resolve_explicit_overrides_env(monkeypatch, small_files):
    """An explicit name wins over the env var."""
    monkeypatch.setenv("COMPARISON_BACKEND", "duckdb")
    f1, f2 = small_files
    assert resolve_backend(f1, f2, "native") == "native"


# ---------------------------------------------------------------------------
# auto resolution
# ---------------------------------------------------------------------------
def test_resolve_auto_small_files_is_native(monkeypatch, small_files):
    """auto + small files (below threshold) -> native, even with duckdb present."""
    monkeypatch.setenv("COMPARISON_BACKEND", "auto")
    f1, f2 = small_files
    assert resolve_backend(f1, f2) == "native"


def test_resolve_auto_large_regime_with_duckdb_is_duckdb(monkeypatch, small_files):
    """auto + large regime + duckdb importable -> duckdb.

    The large regime is forced by mocking ``should_use_chunked`` (the single
    source of truth for the size route) rather than writing a 50 MB file, and
    duckdb importability is forced True via ``find_spec`` so the test is valid
    whether or not the package is installed.
    """
    monkeypatch.setenv("COMPARISON_BACKEND", "auto")
    monkeypatch.setattr(
        "src.comparators.backends.factory.should_use_chunked", lambda p: True
    )
    monkeypatch.setattr(
        "src.comparators.backends.factory.importlib.util.find_spec",
        lambda name: object() if name == "duckdb" else None,
    )
    f1, f2 = small_files
    assert resolve_backend(f1, f2) == "duckdb"


def test_resolve_auto_large_regime_without_duckdb_is_native_no_error(
    monkeypatch, small_files
):
    """auto + large regime but duckdb NOT importable -> native, NO error.

    ``find_spec('duckdb')`` is mocked to return None (duckdb is never actually
    uninstalled), so this proves the silent native fallback regardless of the
    real environment.
    """
    monkeypatch.setenv("COMPARISON_BACKEND", "auto")
    monkeypatch.setattr(
        "src.comparators.backends.factory.should_use_chunked", lambda p: True
    )
    monkeypatch.setattr(
        "src.comparators.backends.factory.importlib.util.find_spec",
        lambda name: None,
    )
    f1, f2 = small_files
    # Must not raise; must resolve to native.
    assert resolve_backend(f1, f2) == "native"


def test_auto_without_duckdb_does_not_import_duckdb(monkeypatch, small_files):
    """The auto-without-duckdb path must not import the duckdb module.

    Mocking ``find_spec`` to None means the resolver never reaches the duckdb
    branch; assert no fresh import of ``duckdb`` was triggered by resolution.
    """
    monkeypatch.setenv("COMPARISON_BACKEND", "auto")
    monkeypatch.setattr(
        "src.comparators.backends.factory.should_use_chunked", lambda p: True
    )
    monkeypatch.setattr(
        "src.comparators.backends.factory.importlib.util.find_spec",
        lambda name: None,
    )
    had_duckdb = "duckdb" in sys.modules
    f1, f2 = small_files
    assert resolve_backend(f1, f2) == "native"
    if not had_duckdb:
        assert "duckdb" not in sys.modules


# ---------------------------------------------------------------------------
# run_compare_service wiring: default byte-identical, explicit override
# ---------------------------------------------------------------------------
def test_service_default_uses_native(monkeypatch, small_files):
    """Env unset + no explicit arg -> native backend; result is the native shape."""
    monkeypatch.delenv("COMPARISON_BACKEND", raising=False)
    f1, f2 = small_files
    result = run_compare_service(file1=f1, file2=f2, keys="customer_id", detailed=True)
    assert result["structure_compatible"] is True
    assert result["total_rows_file1"] == 3
    assert result["rows_with_differences"] == 1


def test_service_default_identical_to_explicit_native(monkeypatch, small_files):
    """Default path == explicitly selecting native (byte-identical contract)."""
    monkeypatch.delenv("COMPARISON_BACKEND", raising=False)
    f1, f2 = small_files
    default = run_compare_service(file1=f1, file2=f2, keys="customer_id")
    explicit = run_compare_service(
        file1=f1, file2=f2, keys="customer_id", backend="native"
    )
    assert default["differences"] == explicit["differences"]
    assert default["matching_rows"] == explicit["matching_rows"]
    assert default["field_statistics"] == explicit["field_statistics"]
    assert default["structure_compatible"] == explicit["structure_compatible"]


def test_service_default_never_imports_duckdb(monkeypatch, small_files):
    """The DEFAULT service path must never import the duckdb module.

    When duckdb is already imported in the session we cannot assert absence, so
    the assertion is skipped in that case (the resolver still does not import it
    on the native path — find_spec is non-importing).
    """
    monkeypatch.delenv("COMPARISON_BACKEND", raising=False)
    had_duckdb = "duckdb" in sys.modules
    f1, f2 = small_files
    run_compare_service(file1=f1, file2=f2, keys="customer_id")
    if not had_duckdb:
        assert "duckdb" not in sys.modules


def test_service_explicit_backend_overrides_env(monkeypatch, small_files):
    """An explicit backend arg to the service wins over the env var."""
    monkeypatch.setenv("COMPARISON_BACKEND", "duckdb")
    f1, f2 = small_files
    # Explicit 'native' must produce the native contract regardless of the env
    # pointing at duckdb.
    result = run_compare_service(
        file1=f1, file2=f2, keys="customer_id", backend="native"
    )
    assert result["structure_compatible"] is True
    assert result["rows_with_differences"] == 1


# ---------------------------------------------------------------------------
# Real duckdb selection + small compare (skip when not installed)
# ---------------------------------------------------------------------------
def test_service_duckdb_explicit_real_compare(small_files):
    """COMPARISON_BACKEND=duckdb selects DuckDB and produces the native contract.

    Runs a real small compare so this is an end-to-end selection proof; skipped
    when the optional ``duckdb`` package is absent.
    """
    pytest.importorskip("duckdb")
    f1, f2 = small_files
    result = run_compare_service(
        file1=f1, file2=f2, keys="customer_id", backend="duckdb"
    )
    assert result["structure_compatible"] is True
    assert result["total_rows_file1"] == 3
    assert result["total_rows_file2"] == 3
    assert result["rows_with_differences"] == 1
    diff = result["differences"][0]
    assert diff["keys"] == {"customer_id": "2"}
    assert diff["differences"]["balance"]["file1"] == "200"
    assert diff["differences"]["balance"]["file2"] == "999"


def test_get_comparison_backend_duckdb_when_installed():
    """The factory resolves 'duckdb' to the DuckDB backend class when present."""
    pytest.importorskip("duckdb")
    from src.comparators.backends.duckdb_backend import DuckDBComparisonBackend

    assert isinstance(get_comparison_backend("duckdb"), DuckDBComparisonBackend)


def test_threshold_constant_is_50mb():
    """Guards the documented 50 MB auto threshold the resolver relies on."""
    assert CHUNK_THRESHOLD_BYTES == 50 * 1024 * 1024
