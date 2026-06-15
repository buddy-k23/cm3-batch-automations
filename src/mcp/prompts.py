"""MCP prompt templates for Valdo (EF-S6).

This module ships the three named workflow prompts that surface in MCP
clients (Claude Desktop, mcp-cli, etc.) and guide an agent through the
correct Valdo tool-call sequence for the most common BA / SRE flows:

* :func:`build_onboard_new_source_messages` — onboard a new source from
  an Excel workbook. Sequences ``upload_workbook_as_spec`` →
  ``onboard_source_dry_run`` → drift summary → user-confirmation gate
  before any write or MR.

* :func:`build_diagnose_validation_failure_messages` — triage a failed or
  partially-failing validation run. Sequences ``get_run_status`` →
  ``get_violations`` → ``taxonomy://violations`` lookup → top-5
  severity-grouped summary + next-step recommendation.

* :func:`build_infer_field_map_messages` — produce a draft mapping from a
  sample file. Wraps ``infer_mapping_from_sample`` and walks the user
  through inferred fields, flagging low-confidence ``FIELD_NNN``
  placeholders that didn't get header-sniffed.

Design notes
------------

The prompts are **plain-text instructional bodies**, not structured
JSON tool-call manifests. The MCP spec models a prompt as a list of
``PromptMessage`` objects that the host LLM reads as context — the
agent then decides which tools to invoke. Keeping the body free-text
mirrors how the official MCP server reference implementations write
their prompts (e.g. ``mcp-server-everything``) and lets us reference
tool names by string without binding to a specific MCP SDK version's
tool-call schema.

Every message produced here is ``role="user"`` — prompt content is
context the host LLM consumes on the user's behalf, not a system
directive. The host's own system prompt continues to govern overall
behaviour.

Tool / resource name discipline
-------------------------------

The prompt bodies reference Valdo tool and resource names verbatim:

* Tools: ``upload_workbook_as_spec``, ``onboard_source_dry_run``,
  ``get_run_status``, ``get_violations``, ``infer_mapping_from_sample``.
* Resources: ``taxonomy://violations``.

The integration test suite
(``tests/integration/test_mcp_prompts.py``) asserts these strings appear
in the rendered messages, so any tool rename in EF-S2/4/5 will fail
the test fast and force a coordinated prompt update — no silent drift
between the registered tool surface and the prompt instructions.
"""

from __future__ import annotations

from textwrap import dedent
from typing import List, Optional

from mcp.server.fastmcp.prompts.base import UserMessage

__all__ = [
    "ONBOARD_NEW_SOURCE_DESCRIPTION",
    "DIAGNOSE_VALIDATION_FAILURE_DESCRIPTION",
    "INFER_FIELD_MAP_DESCRIPTION",
    "build_onboard_new_source_messages",
    "build_diagnose_validation_failure_messages",
    "build_infer_field_map_messages",
]


# One-sentence descriptions exposed in MCP clients' prompt pickers. Kept
# at module scope so the FastMCP registration in ``server.py`` and the
# test assertions both reference the same string.
ONBOARD_NEW_SOURCE_DESCRIPTION = (
    "Onboard a new Valdo source from an Excel workbook: sandbox the "
    "workbook, dry-run the artefact tree, summarise drift, and ask the "
    "user to confirm before any write or MR."
)

DIAGNOSE_VALIDATION_FAILURE_DESCRIPTION = (
    "Diagnose a failed or partially-failing Valdo validation run by "
    "fetching status + violations, looking up violation severities in "
    "the taxonomy, and summarising the top issues with next steps."
)

INFER_FIELD_MAP_DESCRIPTION = (
    "Infer a draft field mapping from a sample CSV / fixed-width file "
    "and walk the user through the inferred fields, flagging "
    "low-confidence placeholder names."
)


def build_onboard_new_source_messages(
    workbook_path: str,
    source_code: Optional[str] = None,
) -> List[UserMessage]:
    """Render the ``onboard_new_source`` prompt messages.

    Args:
        workbook_path: Absolute path to the Excel onboarding workbook the
            BA wants to sandbox + dry-run.
        source_code: Optional override for the inferred source code
            (e.g. ``"SHAW"``). When omitted the tool infers from the
            workbook's ``SourceMetadata`` sheet.

    Returns:
        A list of :class:`UserMessage` instances forming the prompt
        context. The agent reads these and decides when to invoke each
        referenced tool.
    """
    source_clause = (
        f' (override source_code="{source_code}")'
        if source_code
        else " (let the tool infer the source code from SourceMetadata)"
    )
    body = dedent(
        f"""\
        You are helping a Valdo BA onboard a new source from an Excel
        workbook at: {workbook_path}{source_clause}.

        Follow this sequence exactly:

        1. Call the `upload_workbook_as_spec` tool with
           workbook_path="{workbook_path}"{
            f' and source_code="{source_code}"' if source_code else ""
           } to sandbox the workbook under
           ~/.valdo/mcp_sandbox/<SOURCE>/onboarding.xlsx. This is a
           sandbox copy — no committed config is touched.

        2. Call `onboard_source_dry_run` with the sandboxed
           workbook_path returned in step 1 to preview the artefact
           tree that would be written (source YAML, mapping JSON,
           rules JSON, reconciliation YAML, and SQL emitter output).
           This call has no disk side-effects on the committed tree.

        3. Summarise the drift report for the user. The dry-run
           response includes per-artefact `status` (new | changed |
           unchanged) and aggregate counts. Report all three counts
           and call out any `changed` artefacts by path so the BA can
           review what will move.

        4. STOP. Ask the user to confirm before any write to disk or
           MR. Do NOT invoke any commit / push / open-MR tool until
           the user explicitly approves. If the user requests changes
           to the workbook, instruct them to edit the source file and
           re-run this prompt — do not silently re-upload.

        Tool names are exact: `upload_workbook_as_spec`,
        `onboard_source_dry_run`.
        """
    )
    return [UserMessage(content=body)]


def build_diagnose_validation_failure_messages(run_id: str) -> List[UserMessage]:
    """Render the ``diagnose_validation_failure`` prompt messages.

    Args:
        run_id: The Valdo run identifier returned by ``validate_file``
            (or surfaced from the Recent Runs UI).

    Returns:
        A list of :class:`UserMessage` instances guiding the agent
        through run-status check → violation paging → severity-grouped
        summary.
    """
    body = dedent(
        f"""\
        You are helping a Valdo SRE / on-call engineer triage a failed
        or partially-failing validation run: run_id="{run_id}".

        Follow this sequence exactly:

        1. Call the `get_run_status` tool with run_id="{run_id}" to
           confirm the run has finished and inspect its terminal
           status. If the run is still in-flight, report the current
           status to the user and stop — do NOT try to fetch
           violations from an unfinished run.

        2. Call `get_violations` with run_id="{run_id}", page=1, and
           page_size=50 to fetch the first page of violations. Note
           the `total` count returned so the user knows if more pages
           exist.

        3. Fetch the `taxonomy://violations` resource so you can map
           each violation `kind` to its canonical severity and human
           description. Do not invent severity meanings — use the
           taxonomy verbatim.

        4. Group the page-1 violations by severity and rule_id, then
           summarise the TOP 5 issues for the user. For each top
           issue include: rule_id, severity, count, and one
           representative example (first occurrence). Keep the
           summary tight — the SRE needs the bottom line, not a
           dump.

        5. Suggest next steps based on what you see:
           - If most violations are `field_mismatch` against an
             obviously wrong rule, propose editing the mapping/rules
             and re-running.
           - If violations cluster on a specific record_type, propose
             re-running with `--multi-record` scoped to that type.
           - If status is `error` (not just `failed`), propose
             opening a bug — the engine itself crashed.

        Tool names are exact: `get_run_status`, `get_violations`.
        Resource URI is exact: `taxonomy://violations`.
        """
    )
    return [UserMessage(content=body)]


def build_infer_field_map_messages(
    sample_file_path: str,
    file_type: str,
) -> List[UserMessage]:
    """Render the ``infer_field_map`` prompt messages.

    Args:
        sample_file_path: Absolute path to the sample data file
            (CSV, TSV, pipe-delimited, or fixed-width).
        file_type: Logical file type the mapping draft will target
            (e.g. ``"transactions"``, ``"customers"``). Used both by
            the inference tool and as the target sheet name when the
            user pastes the draft into a workbook.

    Returns:
        A list of :class:`UserMessage` instances guiding the agent
        through inference → low-confidence flagging → workbook-paste
        instructions.
    """
    body = dedent(
        f"""\
        You are helping a Valdo BA bootstrap a field mapping draft from
        a sample data file at: {sample_file_path} (file_type="{file_type}").

        Follow this sequence exactly:

        1. Call the `infer_mapping_from_sample` tool with
           sample_file_path="{sample_file_path}" and
           file_type="{file_type}". The tool inspects the sample,
           detects the format (CSV / TSV / pipe-delimited /
           fixed-width), and emits a draft mapping with one entry per
           detected column.

        2. Walk the user through the inferred fields. For each field
           report: position, inferred name, inferred type, and
           whether the name was sniffed from a header row or
           generated as a placeholder.

        3. Flag low-confidence guesses explicitly. Any field whose
           name matches the pattern `FIELD_NNN` (e.g. `FIELD_001`,
           `FIELD_017`) was NOT recognised from a header row — the
           tool fell back to positional naming. Highlight these so
           the BA can rename them by hand before committing.

        4. Tell the user how to incorporate the draft into a Valdo
           onboarding workbook: paste the inferred rows into the
           `{file_type}_Mapping` sheet (one row per field, columns
           matching the workbook template), then re-run the
           `onboard_new_source` flow against the updated workbook.

        Tool name is exact: `infer_mapping_from_sample`.
        """
    )
    return [UserMessage(content=body)]
