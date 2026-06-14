"""Tests for strict validation in bulk_convert_rules script."""

from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile


_SCRIPT = Path('scripts/bulk_convert_rules.py')
_spec = spec_from_file_location('bulk_convert_rules', _SCRIPT)
_mod = module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_ba_template_validation_positive():
    content = (
        'Rule ID,Rule Name,Field,Rule Type,Severity,Expected / Values,Condition (optional),Enabled\n'
        'BR1001,Status in list,status,Allowed Values,Warning,"ACTIVE,INACTIVE",status = ACTIVE,Y\n'
    )
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(content)
        p = Path(f.name)

    issues, kind = _mod.validate_template_strict(p)
    assert kind == 'ba_friendly'
    assert issues == []


def test_ba_template_validation_negative():
    content = (
        'Rule ID,Rule Name,Field,Rule Type,Severity,Expected / Values,Condition (optional),Enabled\n'
        'B!,Bad rule,status,Compare Fields,Sev,>=,status ~~ ACTIVE,Y\n'
    )
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(content)
        p = Path(f.name)

    issues, kind = _mod.validate_template_strict(p)
    assert kind == 'ba_friendly'
    assert len(issues) >= 3
    problems = ' | '.join(i['issue'] for i in issues)
    assert 'Invalid Rule ID format' in problems
    assert 'Invalid severity' in problems


# ---------------------------------------------------------------------------
# Converter dispatch tests (handover open follow-up #7)
#
# Before the fix, convert_file always instantiated RulesTemplateConverter
# and silently mis-converted BA-friendly templates. These tests pin the
# dispatch so a regression is loud.
# ---------------------------------------------------------------------------


def _make_template(content: str) -> Path:
    """Write *content* to a temp .csv and return its path."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(content)
        return Path(f.name)


def test_convert_file_uses_ba_converter_for_ba_friendly_kind(tmp_path):
    """``kind='ba_friendly'`` must route to ``BARulesTemplateConverter``."""
    template = _make_template(
        'Rule ID,Rule Name,Field,Rule Type,Severity,Expected / Values,Enabled\n'
        'BR1001,Status in list,status,Allowed Values,error,"A,B",Y\n'
    )

    with patch.object(_mod, 'BARulesTemplateConverter') as mock_ba, \
            patch.object(_mod, 'RulesTemplateConverter') as mock_std:
        mock_ba.return_value = MagicMock()
        mock_std.return_value = MagicMock()
        _mod.convert_file(template, tmp_path, kind='ba_friendly')

    mock_ba.assert_called_once()
    mock_std.assert_not_called()
    # The single returned BA converter instance must have driven the
    # full convert→save sequence.
    instance = mock_ba.return_value
    instance.from_csv.assert_called_once_with(str(template))
    instance.save.assert_called_once()


def test_convert_file_uses_standard_converter_for_standard_kind(tmp_path):
    """``kind='standard'`` must route to ``RulesTemplateConverter``."""
    template = _make_template(
        'Rule ID,Rule Name,Description,Type,Severity,Operator\n'
        'BR2001,Status not null,Status required,field_validation,error,not_null\n'
    )

    with patch.object(_mod, 'BARulesTemplateConverter') as mock_ba, \
            patch.object(_mod, 'RulesTemplateConverter') as mock_std:
        mock_ba.return_value = MagicMock()
        mock_std.return_value = MagicMock()
        _mod.convert_file(template, tmp_path, kind='standard')

    mock_std.assert_called_once()
    mock_ba.assert_not_called()
    instance = mock_std.return_value
    instance.from_csv.assert_called_once_with(str(template))
    instance.save.assert_called_once()


def test_convert_file_defaults_to_standard_when_kind_omitted(tmp_path):
    """Omitting ``kind`` preserves pre-fix behavior (defensive default)."""
    template = _make_template(
        'Rule ID,Rule Name,Description,Type,Severity,Operator\n'
        'BR2001,Status not null,Status required,field_validation,error,not_null\n'
    )

    with patch.object(_mod, 'BARulesTemplateConverter') as mock_ba, \
            patch.object(_mod, 'RulesTemplateConverter') as mock_std:
        mock_ba.return_value = MagicMock()
        mock_std.return_value = MagicMock()
        _mod.convert_file(template, tmp_path)

    mock_std.assert_called_once()
    mock_ba.assert_not_called()


# ---------------------------------------------------------------------------
# Rule-ID format + Expected / Values relaxation (prompts/generate-rules-csv.md)
#
# The strict validator was originally tuned to a different template
# convention (``BR``-prefixed IDs, ``Expected / Values`` always required).
# Per AGENTS.md, ``prompts/generate-rules-csv.md`` is the authoritative
# spec for this format; it mandates ``R001`` / ``CR001`` and explicitly
# marks ``Expected / Values`` as blank for value-less rule types like
# ``not_empty``. These tests pin the relaxed behavior so a regression to
# the over-strict checks is loud.
# ---------------------------------------------------------------------------


def test_validate_accepts_r_prefix_rule_id():
    """``R001`` is the canonical per-row rule ID per the prompt."""
    content = (
        'Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values\n'
        'R001,ACCT not empty,ACCT,not_empty,error,Yes,ACCT must not be empty,\n'
    )
    p = _make_template(content)
    issues, kind = _mod.validate_template_strict(p)
    assert kind == 'ba_friendly'
    assert issues == []


def test_validate_accepts_cr_prefix_rule_id():
    """``CR001`` is the canonical cross-row rule ID per the prompt."""
    content = (
        'Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values\n'
        'CR001,ACCT unique,ACCT,cross_row:unique,error,Yes,ACCT must be unique,\n'
    )
    p = _make_template(content)
    issues, kind = _mod.validate_template_strict(p)
    assert kind == 'ba_friendly'
    assert issues == []


def test_validate_still_accepts_br_prefix_for_legacy_templates():
    """``BR1001`` continues to validate so older templates do not regress."""
    content = (
        'Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values,Condition (optional)\n'
        'BR1001,Status in list,status,Allowed Values,error,Yes,Status must be A or B,"A,B",\n'
    )
    p = _make_template(content)
    issues, kind = _mod.validate_template_strict(p)
    assert kind == 'ba_friendly'
    assert issues == []


def test_validate_rejects_unrecognised_rule_id_prefix():
    """A free-form Rule ID still fails the format check."""
    content = (
        'Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values\n'
        'XYZ123,ACCT not empty,ACCT,not_empty,error,Yes,ACCT must not be empty,\n'
    )
    p = _make_template(content)
    issues, _kind = _mod.validate_template_strict(p)
    assert any(
        i['field'] == 'Rule ID' and i['issue'] == 'Invalid Rule ID format'
        for i in issues
    ), issues


def test_expected_values_blank_is_allowed_for_not_empty():
    """``not_empty`` rules legitimately carry no value parameter."""
    content = (
        'Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values\n'
        'R001,ACCT not empty,ACCT,not_empty,error,Yes,must not be empty,\n'
        'R002,ACCT numeric,ACCT,numeric,error,Yes,must be numeric,\n'
    )
    p = _make_template(content)
    issues, _kind = _mod.validate_template_strict(p)
    assert issues == []


def test_expected_values_required_for_valid_values():
    """``valid_values`` without a list is a hard error — the rule is meaningless."""
    content = (
        'Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values\n'
        'R001,ACCT codes,ACCT,valid_values,error,Yes,must be one of,\n'
    )
    p = _make_template(content)
    issues, _kind = _mod.validate_template_strict(p)
    assert any(
        i['field'] == 'Expected / Values'
        and "valid_values" in i['issue']
        for i in issues
    ), issues


def test_expected_values_required_for_exact_length():
    """``exact_length`` needs a length value."""
    content = (
        'Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values\n'
        'R001,ACCT length,ACCT,exact_length,error,Yes,must be 18 chars,\n'
    )
    p = _make_template(content)
    issues, _kind = _mod.validate_template_strict(p)
    assert any(i['field'] == 'Expected / Values' for i in issues), issues


def test_expected_values_blank_is_allowed_for_cross_row_unique():
    """``cross_row:unique`` is value-less per the prompt's reference table."""
    content = (
        'Rule ID,Rule Name,Field,Rule Type,Severity,Enabled,Message,Expected / Values\n'
        'CR001,ACCT unique,ACCT,cross_row:unique,error,Yes,must be unique,\n'
    )
    p = _make_template(content)
    issues, _kind = _mod.validate_template_strict(p)
    assert issues == []


def test_convert_file_dispatches_excel_to_ba_converter(tmp_path):
    """Excel inputs route through the same dispatch as CSV inputs.

    The file-extension branch (``.csv`` vs anything else) is internal to
    ``convert_file``; the dispatch axis we care about is the *kind*.
    """
    excel_template = tmp_path / 'ba_template.xlsx'
    excel_template.write_bytes(b'fake-xlsx')  # contents irrelevant under mock

    with patch.object(_mod, 'BARulesTemplateConverter') as mock_ba, \
            patch.object(_mod, 'RulesTemplateConverter') as mock_std:
        mock_ba.return_value = MagicMock()
        mock_std.return_value = MagicMock()
        _mod.convert_file(excel_template, tmp_path, kind='ba_friendly')

    mock_ba.assert_called_once()
    mock_std.assert_not_called()
    instance = mock_ba.return_value
    instance.from_excel.assert_called_once_with(str(excel_template))
    instance.from_csv.assert_not_called()
    instance.save.assert_called_once()
