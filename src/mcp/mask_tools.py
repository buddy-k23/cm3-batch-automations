"""Mask (PII masking) MCP tool implementation (S21-3, #435).

Wires the ``mask_file`` MCP tool onto the existing masking service
(:class:`src.services.masking_service.MaskingService`) — the same code path
the ``valdo mask`` CLI command (:func:`src.commands.mask_command.run_mask_command`)
drives. Like the other action tools (S21-1 ``db_compare`` / S21-2
``reconcile_all``) this module is a thin adapter — no masking logic lives
here; the tool registration itself stays in :mod:`src.mcp.server`.

The tool lets an agent mask the PII in a batch file (fixed-width or
pipe-delimited) by pointing at the source file, the mapping JSON (which
defines field positions / names), and a masking-rules JSON (which maps each
field to one of the six strategies: ``preserve``, ``preserve_format``,
``deterministic_hash``, ``random_range``, ``redact``, ``fake_name``). The
service writes a masked copy to *output* — the original file is never
modified.

PII safety (CRITICAL):
    The masking workflow exists to STRIP PII, so the tool response must not
    re-leak it. The response carries ONLY:

    - the path of the masked output file,
    - the count of records masked,
    - a per-field summary of which strategy was applied (strategy NAMES and
      field NAMES only).

    It NEVER carries raw field values — neither the unmasked originals nor
    the masked replacements. An agent that wants to inspect the masked rows
    reads the output file itself; the values never transit the MCP response.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from mcp.server.fastmcp.exceptions import ToolError

from src.services.masking_service import MaskingService

__all__ = [
    "mask_file_payload",
    "MASK_FILE_DESCRIPTION",
]

logger = logging.getLogger(__name__)

# The six masking strategies the service understands (S21-3 / #435). Kept
# here so the adapter can describe an unknown-strategy summary entry without
# importing the service's private dispatch table.
_KNOWN_STRATEGIES = frozenset(
    {
        "preserve",
        "preserve_format",
        "deterministic_hash",
        "random_range",
        "redact",
        "fake_name",
    }
)


def _load_json(path_str: str, *, label: str) -> Dict[str, Any]:
    """Load and parse a JSON file, surfacing caller-fixable failures.

    Args:
        path_str: Path to the JSON file.
        label: Human-readable name of the artefact (for error messages).

    Returns:
        The parsed JSON object.

    Raises:
        ToolError: When the file is missing or cannot be parsed as JSON.
    """
    path = Path(path_str)
    if not path.exists():
        raise ToolError(f"{label} file not found: {path_str}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ToolError(f"Failed to load {label} file: {exc}") from exc


def _build_strategy_summary(
    mapping_config: Dict[str, Any],
    masking_rules: Dict[str, Any],
) -> List[Dict[str, str]]:
    """Build a per-field strategy summary — names only, NO values.

    Walks the mapping's ``fields`` in declared order and records, for each,
    the masking strategy that will be applied (defaulting to ``preserve``
    when the field has no rule). Only field names and strategy names appear
    in the result — never any field value — so the summary is safe to return
    over MCP for PII-bearing files.

    Args:
        mapping_config: The mapping JSON (its ``fields`` list names the
            columns, in order).
        masking_rules: The masking-rules JSON; its ``fields`` object maps a
            field name to a rule dict carrying a ``strategy``.

    Returns:
        A list of ``{"field": <name>, "strategy": <strategy>}`` dicts, one
        per mapped field, in mapping order.
    """
    field_rules = masking_rules.get("fields", {}) or {}
    summary: List[Dict[str, str]] = []
    for field in mapping_config.get("fields", []) or []:
        name = field.get("name")
        if not name:
            continue
        rule = field_rules.get(name, {}) or {}
        strategy = rule.get("strategy", "preserve")
        if strategy not in _KNOWN_STRATEGIES:
            # Surface the declared (unknown) strategy verbatim so the agent
            # can reason about the typo; the service itself would raise on
            # such a value at mask time, but we only reach here for fields
            # that actually appear in the file, so describing it is safe.
            strategy = str(strategy)
        summary.append({"field": str(name), "strategy": strategy})
    return summary


def mask_file_payload(
    file: str,
    mapping: str,
    masking_config: str,
    output: str,
) -> Dict[str, Any]:
    """Mask the PII in a batch file and return a PII-safe summary.

    Thin adapter over :meth:`src.services.masking_service.MaskingService.mask_file`.
    The mapping JSON (field positions / names) and the masking-rules JSON
    (per-field strategy config) are loaded here; the masking itself, the
    format detection, and the output-file plumbing all live in the service
    layer. The original *file* is never modified — the service writes a
    masked copy to *output*.

    Args:
        file: Path to the input batch file (fixed-width or pipe-delimited).
            Never modified.
        mapping: Path to the mapping JSON. Defines field names and (for
            fixed-width) positions / lengths via its ``fields`` list and
            ``source.format``.
        masking_config: Path to the masking-rules JSON. Its ``fields`` object
            maps each field name to a rule dict carrying a ``strategy`` (one
            of the six: ``preserve``, ``preserve_format``,
            ``deterministic_hash``, ``random_range``, ``redact``,
            ``fake_name``). Fields with no rule are preserved unchanged.
        output: Destination path for the masked output file. Parent
            directories are created if needed.

    Returns:
        A PII-safe summary dict with exactly three keys:

        - ``output_path``: the path the masked file was written to.
        - ``records_masked``: the count of records processed.
        - ``field_strategies``: a per-field list of
          ``{"field": <name>, "strategy": <strategy>}`` — strategy and field
          NAMES only.

        The response NEVER carries raw field values (masked or unmasked).

    Raises:
        ToolError: When any of *file* / *mapping* / *output* is missing or
            blank, the mapping or masking-config file cannot be found /
            parsed, the input file does not exist, the mapping format is
            unsupported, or a declared masking strategy is unrecognised. All
            of these are caller-fixable problems.
    """
    if not file or not isinstance(file, str):
        raise ToolError("file is required and must be a non-empty string")
    if not mapping or not isinstance(mapping, str):
        raise ToolError("mapping is required and must be a non-empty string")
    if not masking_config or not isinstance(masking_config, str):
        raise ToolError(
            "masking_config is required and must be a non-empty string"
        )
    if not output or not isinstance(output, str):
        raise ToolError("output is required and must be a non-empty string")

    mapping_config = _load_json(mapping, label="Mapping")
    masking_rules = _load_json(masking_config, label="Masking config")

    # Build the strategy summary BEFORE masking so a mask-time failure still
    # leaves us nothing PII-bearing to leak — and it draws only on the two
    # config docs, never on the data file.
    field_strategies = _build_strategy_summary(mapping_config, masking_rules)

    try:
        result = MaskingService().mask_file(
            file, output, mapping_config, masking_rules
        )
    except (FileNotFoundError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface infra failures as ToolError
        logger.exception("mask_file tool failed for file=%r", file)
        raise ToolError(f"Masking failed: {exc}") from exc

    # PII safety: return paths + counts + strategy names ONLY. The service's
    # result dict carries only ``records_masked`` and ``output_path`` (no
    # values), and we re-shape it explicitly so a future service change that
    # adds value-bearing keys cannot silently leak through this adapter.
    return {
        "output_path": result["output_path"],
        "records_masked": result["records_masked"],
        "field_strategies": field_strategies,
    }


MASK_FILE_DESCRIPTION = (
    "Mask the PII in a batch file (fixed-width or pipe-delimited) and write a "
    "masked copy, returning a PII-safe summary. Accepts the input file path, "
    "a mapping JSON path (its 'fields' list names the columns and, for "
    "fixed-width, their positions/lengths), a masking_config JSON path (its "
    "'fields' object maps each field to one of six strategies: preserve, "
    "preserve_format, deterministic_hash, random_range, redact, fake_name; "
    "unmapped fields are preserved), and an output path for the masked file. "
    "Wraps the existing masking service — the same code path the 'valdo mask' "
    "CLI uses; the original input file is never modified. Returns 'output_path' "
    "(where the masked file was written), 'records_masked' (count), and "
    "'field_strategies' (a per-field list of {field, strategy} NAMES). For PII "
    "safety the response NEVER contains raw field values — neither the "
    "unmasked originals nor the masked replacements; read the output file to "
    "inspect rows. Raises a tool error only for caller-fixable problems: a "
    "missing/blank argument, a mapping or masking-config file that cannot be "
    "found or parsed, an input file that does not exist, an unsupported "
    "mapping format, or an unrecognised masking strategy."
)
