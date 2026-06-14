"""Tests for Alembic migration 0001 — APP_RUN_HISTORY and APP_RUN_TESTS.

The local ``alembic/`` directory at project root shadows the installed alembic
package on sys.path, which prevents ``from alembic import op`` from resolving
inside migration files when exec'd from tests.  All tests here therefore use
one of two safe strategies:

1. **AST inspection** — parse the migration source with ``ast`` and inspect
   the tree structurally (column names, function definitions, call counts).
   No imports from the migration module are executed.

2. **Source-level string checks** — quick sanity checks on the raw source
   text (e.g. table ordering in upgrade/downgrade).

This approach is consistent with the pattern noted in ``alembic/env.py`` and
``tests/unit/test_alembic_install.py``.
"""
import ast
import os

MIGRATION_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "../../alembic/versions/0001_create_app_run_history.py",
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


def _get_module_level_assignments(tree: ast.Module) -> dict:
    """Collect top-level name = value assignments from the module AST.

    Args:
        tree: Parsed AST module node.

    Returns:
        Dict mapping variable name to its constant value (for simple literals).
    """
    assignments = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                    assignments[target.id] = node.value.value
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                    if node.value.value is None:
                        assignments[target.id] = None
    return assignments


def _extract_sa_column_names(tree: ast.Module, table_name: str) -> set:
    """Walk the AST and collect Column name strings for a given create_table call.

    Looks for ``op.create_table('<table_name>', sa.Column('<name>', ...))``
    patterns and returns the set of column name strings found.

    Args:
        tree: Parsed AST of the migration file.
        table_name: The table name literal to search for.

    Returns:
        Set of column name strings defined for that table.
    """
    col_names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "create_table"):
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if not (isinstance(first_arg, ast.Constant) and first_arg.value == table_name):
            continue
        for arg in node.args[1:]:
            if not isinstance(arg, ast.Call):
                continue
            col_func = arg.func
            if not (isinstance(col_func, ast.Attribute) and col_func.attr == "Column"):
                continue
            if arg.args and isinstance(arg.args[0], ast.Constant):
                col_names.add(arg.args[0].value)
    return col_names


def _count_calls(tree: ast.Module, method_name: str) -> int:
    """Count top-level op.<method_name>(...) calls anywhere in the AST.

    Args:
        tree: Parsed AST module node.
        method_name: The attribute name to count (e.g. ``'create_table'``).

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


def _function_names(tree: ast.Module) -> set:
    """Return the set of top-level function names defined in the module.

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


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    """Migration file must be present at the expected path."""
    assert os.path.isfile(MIGRATION_PATH), (
        f"Migration not found at {MIGRATION_PATH}"
    )


# ---------------------------------------------------------------------------
# Revision metadata (parsed from source, avoids import shadowing)
# ---------------------------------------------------------------------------


def test_revision_is_0001():
    """revision module-level variable must equal '0001'."""
    source = _read_source()
    # Quick textual check — fast and unambiguous
    assert 'revision = "0001"' in source or "revision = '0001'" in source, (
        "Migration source must contain: revision = \"0001\""
    )


def test_down_revision_is_none():
    """down_revision module-level variable must be None (first migration)."""
    source = _read_source()
    assert "down_revision = None" in source, (
        "Migration source must contain: down_revision = None"
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
        "upgrade() function not found in migration"
    )


def test_downgrade_function_defined():
    """downgrade() function must be defined in the migration module."""
    tree = _parse_source()
    assert "downgrade" in _function_names(tree), (
        "downgrade() function not found in migration"
    )


# ---------------------------------------------------------------------------
# Table / column structure
# ---------------------------------------------------------------------------


def test_run_history_columns_defined():
    """APP_RUN_HISTORY create_table call must define all expected columns."""
    tree = _parse_source()
    col_names = _extract_sa_column_names(tree, "APP_RUN_HISTORY")

    expected = {
        "run_id",
        "suite_name",
        "environment",
        "run_timestamp",
        "status",
        "pass_count",
        "fail_count",
        "skip_count",
        "total_count",
        "report_url",
        "archive_path",
        "created_ts",
    }
    assert expected.issubset(col_names), (
        f"Missing columns in APP_RUN_HISTORY: {expected - col_names}"
    )


def test_run_tests_columns_defined():
    """APP_RUN_TESTS create_table call must define all expected columns."""
    tree = _parse_source()
    col_names = _extract_sa_column_names(tree, "APP_RUN_TESTS")

    expected = {
        "test_id",
        "run_id",
        "test_name",
        "test_type",
        "status",
        "row_count",
        "error_count",
        "duration_secs",
        "report_path",
        "created_ts",
    }
    assert expected.issubset(col_names), (
        f"Missing columns in APP_RUN_TESTS: {expected - col_names}"
    )


def test_create_table_calls_present():
    """upgrade() must contain exactly two create_table calls."""
    tree = _parse_source()
    count = _count_calls(tree, "create_table")
    assert count == 2, f"Expected 2 create_table calls, found {count}"


def test_drop_table_calls_present():
    """downgrade() must contain exactly two drop_table calls."""
    tree = _parse_source()
    count = _count_calls(tree, "drop_table")
    assert count == 2, f"Expected 2 drop_table calls, found {count}"


def test_create_index_calls_present():
    """upgrade() must create both expected indexes."""
    source = _read_source()
    assert "IDX_RUN_HISTORY_TS" in source, (
        "Index IDX_RUN_HISTORY_TS not found in migration"
    )
    assert "IDX_RUN_TESTS_RUN_ID" in source, (
        "Index IDX_RUN_TESTS_RUN_ID not found in migration"
    )


def test_run_history_is_first_table_created():
    """APP_RUN_HISTORY must be created before APP_RUN_TESTS (FK dependency)."""
    source = _read_source()
    history_pos = source.index("APP_RUN_HISTORY")
    tests_pos = source.index("APP_RUN_TESTS")
    assert history_pos < tests_pos, (
        "APP_RUN_HISTORY must appear before APP_RUN_TESTS in the source "
        "(parent table must be created first)"
    )


def test_run_tests_is_dropped_first():
    """APP_RUN_TESTS must be dropped before APP_RUN_HISTORY in downgrade()."""
    source = _read_source()
    downgrade_block = source[source.index("def downgrade"):]
    tests_drop_pos = downgrade_block.index("APP_RUN_TESTS")
    history_drop_pos = downgrade_block.index("APP_RUN_HISTORY")
    assert tests_drop_pos < history_drop_pos, (
        "APP_RUN_TESTS must be dropped before APP_RUN_HISTORY in downgrade() "
        "(child table must be dropped before parent)"
    )


def test_foreign_key_defined():
    """APP_RUN_TESTS must reference APP_RUN_HISTORY via a ForeignKey."""
    source = _read_source()
    assert "APP_RUN_HISTORY.run_id" in source or "ForeignKey" in source, (
        "APP_RUN_TESTS must define a ForeignKey to APP_RUN_HISTORY"
    )
    assert "ForeignKey" in source, "ForeignKey must be used in the migration"


def test_uses_dialect_agnostic_types():
    """Migration must use dialect-agnostic SQLAlchemy types, not Oracle-specific ones in code.

    Only checks the code region (after the module docstring ends) so that
    explanatory references to Oracle type names in comments/docstrings are
    permitted.
    """
    source = _read_source()
    # Skip past the module docstring to get to the actual code
    # The docstring ends at the first triple-quote close after position 3
    doc_end = source.find('"""', 3) + 3
    code_region = source[doc_end:]

    # Oracle-specific type keywords must not appear in actual code
    oracle_types = ["VARCHAR2", "NUMBER(", "TIMESTAMP WITH TIME ZONE"]
    for oracle_type in oracle_types:
        assert oracle_type not in code_region, (
            f"Oracle-specific type '{oracle_type}' found in migration code — "
            "use dialect-agnostic types (String, Integer, Numeric, DateTime)"
        )
    # Ensure the expected generic types ARE used
    assert "sa.String" in code_region, "sa.String must be used for VARCHAR columns"
    assert "sa.Integer" in code_region, "sa.Integer must be used for integer columns"
    assert "sa.DateTime" in code_region, "sa.DateTime must be used for timestamp columns"
    assert "sa.Numeric" in code_region, "sa.Numeric must be used for decimal columns"
