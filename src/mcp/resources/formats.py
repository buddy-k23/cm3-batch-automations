"""``formats://supported`` MCP resource (S7-3, #381).

Single MCP resource that enumerates every Valdo input format an agent can
ask the engine to validate today, plus the formats slated for upcoming
ADR work. The resource is intentionally **declarative** — both lists are
module-level constants at the top of this file so adding a new format
when an ADR closes is a one-line patch with no behavioural change to the
handler.

Resource shape::

    {
      "supported_today": [
        {"format": "<name>", "since": "<Sprint N>",
         "template": "templates://etl/<shape>" | null},
        ...
      ],
      "planned": [
        {"format": "<name>",
         "issue": "https://github.com/buddy-k23/valdo/issues/<n>"},
        ...
      ],
    }

Source of truth
---------------

The two lists are :data:`SUPPORTED_TODAY` and :data:`PLANNED`. Both are
**module-level constants** (``list[dict]``); the resource handler does no
filesystem or DB I/O at request time so the response budget is bounded
and deterministic.

The ``template`` field on a ``supported_today`` entry, when non-null,
**must** resolve through the sibling ``templates://etl/<shape>`` resource
landed in S7-2 (see :mod:`src.mcp.resources.etl_templates`). The
integration test ``test_mcp_formats_resource.py`` pins this contract:
every non-null template URI must reference a discoverable shape, so a
stale entry surfaces as a failing test rather than a 404 to an agent.

The ``issue`` field on a ``planned`` entry references a real GitHub
issue tracking the ADR for that format. The integration test pins the
URL shape (``https://github.com/buddy-k23/valdo/issues/<digits>``) so a
placeholder cannot accidentally land.

Why not auto-discover from the templates directory? Two reasons:

1. **Format != template.** A format the engine supports today may have
   no public template yet (``pipe_delimited`` is the worked example —
   the parser exists, but no committed ``templates/etl/`` shape covers
   it). Driving the list off ``templates/etl/*.yml`` would silently
   hide this format from agents.
2. **Planned formats have no on-disk artefact.** ``json``, ``xml``, and
   ``db_to_db`` are tracked only by their ADR issues. Module-level
   constants are the simplest source-of-truth that surfaces both
   "supported but undocumented" and "planned but not built" rows.
"""

from __future__ import annotations

from typing import Any, Dict, List

__all__ = [
    "SUPPORTED_TODAY",
    "PLANNED",
    "formats_supported_payload",
]


# ---------------------------------------------------------------------------
# Source-of-truth constants.
#
# Update these two lists when a new format ships or an ADR opens. Every
# edit is a one-line change; the resource handler below is a pure
# read-out of these constants and needs no edits.
#
# Conventions:
#
# * ``format`` is the lower-snake-case engine identifier (matches the
#   FileFormat / parser registry naming).
# * ``since`` is the sprint in which the format's first BA-facing
#   surface landed (parser + worked template if applicable). This is
#   informational — agents render it for context, no test pins it.
# * ``template`` is either a fully-qualified ``templates://etl/<shape>``
#   URI that resolves through :mod:`src.mcp.resources.etl_templates`, or
#   ``None`` if no public template covers this format yet. ``None``
#   means "format is supported by the engine but the BA has no
#   ready-made template to start from" — an agent should fall back to
#   the inline mapping/rules workflow or to ``infer_mapping_from_sample``.
# ---------------------------------------------------------------------------

SUPPORTED_TODAY: List[Dict[str, Any]] = [
    {
        "format": "fixed_width_single",
        "since": "Sprint 1",
        "template": "templates://etl/fixed_width_single_record",
    },
    {
        # The multi-record TRANERT template referenced in the original
        # issue example is a SHAW-internal artefact, not a public
        # ``templates/etl/`` shape — so we expose ``null`` here rather
        # than a broken URI. When a generic public multi-record
        # template lands, swap this null for its URI in one line.
        "format": "fixed_width_multi_record",
        "since": "Sprint 2",
        "template": None,
    },
    {
        "format": "csv",
        "since": "Sprint 1",
        "template": "templates://etl/csv_file_comparison",
    },
    {
        # The pipe-delimited parser has shipped since Sprint 1 (see
        # ``src/parsers/pipe_delimited_parser.py``) but no public
        # template covers it yet — agents should fall back to
        # ``infer_mapping_from_sample`` for now.
        "format": "pipe_delimited",
        "since": "Sprint 1",
        "template": None,
    },
    {
        "format": "db_to_file",
        "since": "Sprint 3",
        "template": "templates://etl/db_to_file_reconciliation",
    },
]


# Planned formats: each entry MUST reference a real, open GitHub issue.
# The integration test asserts the URL matches
# ``https://github.com/buddy-k23/valdo/issues/<digits>`` so a placeholder
# (or a typo'd issue number) cannot ship.
PLANNED: List[Dict[str, Any]] = [
    {
        "format": "json",
        "issue": "https://github.com/buddy-k23/valdo/issues/377",
    },
    {
        "format": "xml",
        "issue": "https://github.com/buddy-k23/valdo/issues/378",
    },
    {
        "format": "db_to_db",
        "issue": "https://github.com/buddy-k23/valdo/issues/379",
    },
]


def formats_supported_payload() -> Dict[str, List[Dict[str, Any]]]:
    """Return the payload for ``formats://supported``.

    The handler is intentionally a thin read-out of the module-level
    :data:`SUPPORTED_TODAY` and :data:`PLANNED` constants — no
    filesystem reads, no DB lookups, no caching layer. This keeps the
    response budget bounded and deterministic, and means adding a new
    format is a one-line edit to the constants above with no other
    code change.

    Returns:
        A dict with exactly two keys, ``supported_today`` and
        ``planned``. Each value is a list of dicts copied from the
        module-level constants — the copy guards against an agent (or
        a test) mutating the constants in place.
    """
    # Return shallow copies so a downstream mutation cannot leak back
    # into the module-level constants. Each entry is itself a flat
    # dict of immutable primitives, so a shallow copy of the list +
    # copies of the entries is sufficient.
    return {
        "supported_today": [dict(entry) for entry in SUPPORTED_TODAY],
        "planned": [dict(entry) for entry in PLANNED],
    }
