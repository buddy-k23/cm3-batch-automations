"""Tests for Alembic migration 0002 — idempotency guards for pre-existing tables.

Uses the same AST-inspection strategy as ``test_migration_0001.py`` to avoid
the ``alembic/`` directory shadowing the installed ``alembic`` package on
``sys.path``.  No imports from the migration module are executed.

Revision 0002 must:
- Declare ``revision = '0002'`` and ``down_revision = '0001'``.
- Define ``upgrade()`` and ``downgrade()`` functions.
- Define ``_table_exists`` and ``_column_exists`` helper functions.
- Use ``op.add_column`` calls (guarded by existence checks) in ``upgrade()``.
- Add ``quality_score`` and ``run_duration_seconds`` columns idempotently.
- Implement ``downgrade()`` as a no-op (columns may be pre-existing).
"""

import ast
import os

MIGRATION_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "../../alembic/versions/0002_idempotency_guards.py",
    )
)


def _read_source() -> str:
    """Return the raw source text of the migration file.

    Returns:
        Source code string.
    """
    with open(MIGRATION_PATH) as fh:
        return fh.read()


def _parse_source() -> ast.Module:
    """Parse the migration source into an AST module node.

    Returns:
        Parsed AST module.
    """
    return ast.parse(_read_source())


def _function_names(tree: ast.Module) -> set:
    """Return the set of all function names defined in the module.

    Args:
        tree: Parsed AST module node.

    Returns:
        Set of function name strings.
    """
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }


def _count_calls(tree: ast.Module, method_name: str) -> int:
    """Count ``op.<method_name>(...)`` calls anywhere in the AST.

    Args:
        tree: Parsed AST module node.
        method_name: The attribute name to count (e.g. ``'add_column'``).

    Returns:
        Number of matching call nodes found.
    """
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method_name
    )


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    """Migration file must be present at the expected path."""
    assert os.path.isfile(MIGRATION_PATH), (
        f"Migration 0002 not found at {MIGRATION_PATH}"
    )


# ---------------------------------------------------------------------------
# Revision metadata
# ---------------------------------------------------------------------------


def test_revision_is_0002():
    """revision module-level variable must equal '0002'."""
    source = _read_source()
    assert 'revision = "0002"' in source or "revision = '0002'" in source, (
        "Migration source must contain: revision = \"0002\""
    )


def test_down_revision_is_0001():
    """down_revision module-level variable must equal '0001'."""
    source = _read_source()
    assert 'down_revision = "0001"' in source or "down_revision = '0001'" in source, (
        "Migration source must contain: down_revision = \"0001\""
    )


def test_branch_labels_is_none():
    """branch_labels module-level variable must be None."""
    source = _read_source()
    assert "branch_labels = None" in source, (
        "Migration source must contain: branch_labels = None"
    )


def test_depends_on_is_none():
    """depends_on module-level variable must be None."""
    source = _read_source()
    assert "depends_on = None" in source, (
        "Migration source must contain: depends_on = None"
    )


# ---------------------------------------------------------------------------
# Function presence
# ---------------------------------------------------------------------------


def test_upgrade_function_defined():
    """upgrade() function must be defined in the migration module."""
    tree = _parse_source()
    assert "upgrade" in _function_names(tree), (
        "upgrade() function not found in migration 0002"
    )


def test_downgrade_function_defined():
    """downgrade() function must be defined in the migration module."""
    tree = _parse_source()
    assert "downgrade" in _function_names(tree), (
        "downgrade() function not found in migration 0002"
    )


def test_table_exists_helper_defined():
    """_table_exists() helper must be defined in the migration module."""
    tree = _parse_source()
    assert "_table_exists" in _function_names(tree), (
        "_table_exists() helper not found in migration 0002"
    )


def test_column_exists_helper_defined():
    """_column_exists() helper must be defined in the migration module."""
    tree = _parse_source()
    assert "_column_exists" in _function_names(tree), (
        "_column_exists() helper not found in migration 0002"
    )


# ---------------------------------------------------------------------------
# upgrade() DDL calls
# ---------------------------------------------------------------------------


def test_add_column_calls_present():
    """upgrade() must contain at least two op.add_column calls."""
    tree = _parse_source()
    count = _count_calls(tree, "add_column")
    assert count >= 2, (
        f"Expected at least 2 op.add_column calls in upgrade(), found {count}"
    )


def test_quality_score_column_guarded():
    """upgrade() must add quality_score column with an idempotency guard."""
    source = _read_source()
    assert "quality_score" in source, (
        "quality_score column not found in migration 0002"
    )
    # Guard must check _table_exists and _column_exists before add_column
    assert "_column_exists" in source, (
        "_column_exists() must be used to guard add_column calls"
    )


def test_run_duration_seconds_column_guarded():
    """upgrade() must add run_duration_seconds column with an idempotency guard."""
    source = _read_source()
    assert "run_duration_seconds" in source, (
        "run_duration_seconds column not found in migration 0002"
    )


def test_upgrade_targets_cm3_run_history():
    """upgrade() must apply idempotency guards to CM3_RUN_HISTORY."""
    source = _read_source()
    assert "CM3_RUN_HISTORY" in source, (
        "CM3_RUN_HISTORY table not referenced in migration 0002"
    )


# ---------------------------------------------------------------------------
# downgrade() is a no-op
# ---------------------------------------------------------------------------


def test_downgrade_has_no_drop_calls():
    """downgrade() must NOT call op.drop_column or op.drop_table.

    These columns may have been pre-existing — removing them would be
    destructive and incorrect.
    """
    tree = _parse_source()
    drop_col_count = _count_calls(tree, "drop_column")
    drop_table_count = _count_calls(tree, "drop_table")
    assert drop_col_count == 0, (
        f"downgrade() must not call op.drop_column (found {drop_col_count} calls)"
    )
    assert drop_table_count == 0, (
        f"downgrade() must not call op.drop_table (found {drop_table_count} calls)"
    )


# ---------------------------------------------------------------------------
# Idempotency guard pattern
# ---------------------------------------------------------------------------


def test_table_exists_called_in_upgrade():
    """upgrade() must call _table_exists() to guard column additions."""
    source = _read_source()
    # Verify the guard function is actually invoked (not just defined)
    upgrade_block = source[source.index("def upgrade"):]
    assert "_table_exists(" in upgrade_block, (
        "_table_exists() must be called inside upgrade() to guard DDL"
    )


def test_column_exists_called_in_upgrade():
    """upgrade() must call _column_exists() before each add_column."""
    source = _read_source()
    upgrade_block = source[source.index("def upgrade"):]
    assert "_column_exists(" in upgrade_block, (
        "_column_exists() must be called inside upgrade() to guard add_column"
    )


def test_uses_dialect_agnostic_types():
    """Migration must use dialect-agnostic SQLAlchemy types in code.

    Oracle-specific type names must not appear in the code region
    (comments/docstrings mentioning them are fine).
    """
    source = _read_source()
    # Skip past the module docstring
    doc_end = source.find('"""', 3) + 3
    code_region = source[doc_end:]

    oracle_types = ["VARCHAR2", "NUMBER(", "TIMESTAMP WITH TIME ZONE"]
    for oracle_type in oracle_types:
        assert oracle_type not in code_region, (
            f"Oracle-specific type '{oracle_type}' found in migration code — "
            "use dialect-agnostic types (String, Integer, Numeric, DateTime)"
        )
    assert "sa.Numeric" in code_region, (
        "sa.Numeric must be used for the decimal columns added by 0002"
    )
