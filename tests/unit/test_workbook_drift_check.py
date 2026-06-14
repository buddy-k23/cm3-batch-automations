"""Unit tests for the EC-S11 workbook drift CI guardrail.

The actual GitHub Actions workflow is not executed here (no ``act``
dependency); we test the pure-Python filter helpers in
:mod:`scripts.check_workbook_drift` (allowlist parsing, drift-line
identification, partitioning of CLI output) and assert the workflow
YAML parses as valid YAML with the required top-level keys.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_workbook_drift.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "workbook-drift-check.yml"
ALLOWLIST_PATH = (
    REPO_ROOT / ".github" / "workflows" / "workbook-drift-allowlist.txt"
)


def _load_module():
    """Load ``scripts/check_workbook_drift.py`` as a module.

    ``scripts/`` is not a Python package -- we import via spec_from_file_location
    so the unit tests can reach the filter helpers without requiring
    ``scripts/__init__.py``.

    Returns:
        The loaded module object.
    """
    spec = importlib.util.spec_from_file_location(
        "check_workbook_drift", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_workbook_drift"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def drift_module():
    """Module-scoped helper-module fixture so the import cost is paid once."""
    return _load_module()


# ---------------------------------------------------------------------------
# Allowlist filter -- the core acceptance criteria.
# ---------------------------------------------------------------------------


def test_allowlist_filters_known_carve_out(drift_module):
    """SHAW R028B drift line MUST be tolerated by the committed allowlist."""
    output = (
        "onboard-source --check: SHAW\n"
        "  drift /repo/config/rules/SHAW_TRANERT_CUS_rules.json: "
        "value for key 'rules' differs\n"
        "  64 of 65 artefacts match.\n"
    )
    allowlist = ["config/rules/SHAW_TRANERT_CUS_rules.json"]
    tolerated, unexpected = drift_module.filter_drift_lines(output, allowlist)
    assert len(tolerated) == 1
    assert len(unexpected) == 0
    assert "SHAW_TRANERT_CUS_rules.json" in tolerated[0]


def test_unknown_drift_fails(drift_module):
    """A drift line matching no allowlist substring MUST be flagged."""
    output = (
        "onboard-source --check: ACME\n"
        "  drift /repo/config/mappings/ACME_HEADER.json: "
        "value for key 'fields' differs\n"
        "  drift /repo/config/rules/ACME_HEADER_rules.json: "
        "committed has extra key(s): ['new_rule']\n"
        "  62 of 64 artefacts match.\n"
    )
    allowlist = ["config/rules/SHAW_TRANERT_CUS_rules.json"]
    tolerated, unexpected = drift_module.filter_drift_lines(output, allowlist)
    assert tolerated == []
    assert len(unexpected) == 2
    assert any("ACME_HEADER.json" in line for line in unexpected)
    assert any("ACME_HEADER_rules.json" in line for line in unexpected)


def test_no_drift_passes(drift_module):
    """A clean --check output produces zero drift lines on both sides."""
    output = (
        "onboard-source --check: SHAW\n"
        "  all 65 artefacts match committed state.\n"
    )
    allowlist = ["config/rules/SHAW_TRANERT_CUS_rules.json"]
    tolerated, unexpected = drift_module.filter_drift_lines(output, allowlist)
    assert tolerated == []
    assert unexpected == []


# ---------------------------------------------------------------------------
# Edge cases on the filter helpers.
# ---------------------------------------------------------------------------


def test_empty_output_is_clean(drift_module):
    """Empty CLI output is a no-drift state."""
    tolerated, unexpected = drift_module.filter_drift_lines("", ["whatever"])
    assert tolerated == []
    assert unexpected == []


def test_empty_allowlist_treats_every_drift_as_unexpected(drift_module):
    """An empty allowlist must NOT tolerate any drift line."""
    output = (
        "  drift config/rules/SHAW_TRANERT_CUS_rules.json: "
        "value for key 'rules' differs\n"
    )
    tolerated, unexpected = drift_module.filter_drift_lines(output, [])
    assert tolerated == []
    assert len(unexpected) == 1


def test_summary_line_not_classified_as_drift(drift_module):
    """``  N of M artefacts match.`` is informational, not drift."""
    assert drift_module.is_drift_line("  64 of 65 artefacts match.") is False


def test_drift_line_detected_with_or_without_leading_whitespace(drift_module):
    """The drift-line detector tolerates the CLI's leading two-space indent."""
    assert drift_module.is_drift_line("  drift /tmp/x.json: foo") is True
    assert drift_module.is_drift_line("drift /tmp/x.json: foo") is True


def test_blank_allowlist_entries_ignored(drift_module):
    """Empty substrings in the allowlist must not match every line."""
    output = (
        "  drift config/mappings/ACME_HEADER.json: value differs\n"
    )
    # Empty string would otherwise match every line via "in".
    tolerated, unexpected = drift_module.filter_drift_lines(output, ["", "  ", None])
    assert tolerated == []
    assert len(unexpected) == 1


# ---------------------------------------------------------------------------
# Allowlist file parsing.
# ---------------------------------------------------------------------------


def test_load_allowlist_skips_comments_and_blank_lines(drift_module, tmp_path):
    """The allowlist parser must skip ``#`` comment lines and blank lines."""
    allowlist_file = tmp_path / "allow.txt"
    allowlist_file.write_text(
        "# this is a comment\n"
        "\n"
        "   # indented comment\n"
        "config/rules/SHAW_TRANERT_CUS_rules.json\n"
        "\n"
        "another/path/value.json\n",
        encoding="utf-8",
    )
    entries = drift_module.load_allowlist(allowlist_file)
    assert entries == [
        "config/rules/SHAW_TRANERT_CUS_rules.json",
        "another/path/value.json",
    ]


def test_load_allowlist_missing_file_returns_empty(drift_module, tmp_path):
    """A missing allowlist file behaves like 'tolerate nothing'."""
    entries = drift_module.load_allowlist(tmp_path / "does-not-exist.txt")
    assert entries == []


def test_committed_allowlist_contains_shaw_carve_out(drift_module):
    """The actual committed allowlist MUST tolerate the SHAW R028B drift."""
    entries = drift_module.load_allowlist(ALLOWLIST_PATH)
    assert any(
        "SHAW_TRANERT_CUS_rules.json" in entry for entry in entries
    ), (
        f"committed allowlist {ALLOWLIST_PATH} must carry the SHAW "
        "R028B carve-out per EC-S9 / EC-S10."
    )


# ---------------------------------------------------------------------------
# Workbook discovery.
# ---------------------------------------------------------------------------


def test_discover_workbooks_excludes_blank_template(drift_module, tmp_path):
    """The blank scaffold template must be excluded from discovery."""
    (tmp_path / "source_onboarding_template.xlsx").write_text("")
    (tmp_path / "SHAW_onboarding.xlsx").write_text("")
    (tmp_path / "ACME_onboarding.xlsx").write_text("")
    (tmp_path / "README.md").write_text("ignored")
    workbooks = drift_module.discover_workbooks(tmp_path)
    names = [wb.name for wb in workbooks]
    assert "source_onboarding_template.xlsx" not in names
    assert names == ["ACME_onboarding.xlsx", "SHAW_onboarding.xlsx"]


def test_discover_workbooks_missing_dir_returns_empty(drift_module, tmp_path):
    """A missing templates directory returns an empty list (not a crash)."""
    workbooks = drift_module.discover_workbooks(tmp_path / "nope")
    assert workbooks == []


def test_committed_templates_dir_has_at_least_shaw(drift_module):
    """The real templates/ directory must include SHAW_onboarding.xlsx."""
    workbooks = drift_module.discover_workbooks(REPO_ROOT / "templates")
    names = [wb.name for wb in workbooks]
    assert "SHAW_onboarding.xlsx" in names
    assert "source_onboarding_template.xlsx" not in names


# ---------------------------------------------------------------------------
# Workflow YAML validity.
# ---------------------------------------------------------------------------


def test_workflow_yaml_parses():
    """The GitHub Actions workflow file must be valid YAML."""
    assert WORKFLOW_PATH.exists(), f"missing workflow file: {WORKFLOW_PATH}"
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_workflow_has_required_top_level_keys():
    """The workflow MUST declare name, triggers (``on``), and ``jobs``."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    # PyYAML parses the bare YAML key ``on:`` as the Python boolean
    # ``True`` (per the YAML 1.1 spec) -- accept either form.
    on_key = "on" if "on" in data else True
    assert "name" in data
    assert on_key in data
    assert "jobs" in data
    assert "workbook-drift-check" in data["jobs"]


def test_workflow_triggers_pr_and_push():
    """The workflow must trigger on both ``pull_request`` and ``push``."""
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    triggers = data.get("on", data.get(True))
    assert isinstance(triggers, dict)
    assert "pull_request" in triggers
    assert "push" in triggers


def test_workflow_invokes_check_script():
    """The workflow must invoke ``scripts/check_workbook_drift.sh``.

    Catches accidental refactors where someone inlines the drift logic
    in YAML and bypasses the local-equivalent script. The single-source
    contract is the whole point of EC-S11.
    """
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "scripts/check_workbook_drift.sh" in raw
