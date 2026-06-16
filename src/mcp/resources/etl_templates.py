"""Auto-discovered ``templates://etl/*`` MCP resources (S7-2).

This module is the engine-side adapter behind three MCP resources that
let an agent browse the committed ETL templates without scraping the
filesystem itself:

* ``templates://etl/list`` — JSON list of every discovered template
  shape with a one-line description.
* ``templates://etl/<shape>`` — the raw YAML body of the template (text
  content; the agent receives the file content verbatim so it can be
  copy-pasted into a working spec).
* ``templates://etl/<shape>/sample`` — a manifest of the paired
  ``<shape>_sample/`` directory: each file's relative path, size in
  bytes, and a short text preview (or ``null`` for binary files).

Mirrors the EF-S3 pattern in :mod:`src.mcp.taxonomy`: the *list* of
discovered shapes is introspected from the filesystem at every read so
adding a new ``templates/etl/<shape>.yml`` requires zero code changes
here. Only the loose convention for description sourcing lives in this
module.

Description-sourcing convention
-------------------------------

For each discovered template the one-line description is resolved with
this priority (the first non-empty hit wins):

1. The top-level ``description:`` field inside the YAML, parsed via
   ``yaml.safe_load``. This is the canonical field (it is modelled on
   :class:`src.pipeline.etl_config.SourceConfig`) and every committed
   template under ``templates/etl/`` carries one as of S7-1.
2. The first non-comment paragraph of the paired ``<shape>_README.md``
   (specifically: the text following the first H1 line, stripped of
   blank lines, truncated at the first blank line so multi-paragraph
   READMEs don't bleed into the resource).
3. Literal ``"(no description available)"`` placeholder — surfaced to
   the agent so a missing description is visible rather than silently
   omitted.

Why ``description:`` is the canonical source: it is the only field that
is (a) already present in every committed template, (b) part of the
:class:`SourceConfig` Pydantic contract, and therefore (c) test-pinned
not to silently drift. README headers are useful but less constrained;
inline ``# Description:`` header comments are not currently used by any
committed template and add a parallel convention we'd have to keep in
sync.

Failure handling
----------------

A template that fails to load through ``SourceConfig.model_validate()``
is **omitted from the list** rather than crashing discovery, and the
failure is logged at WARNING level (with the path + exception type) so
operators see the gap. This mirrors the fail-soft posture used by
:mod:`src.mcp.run_registry` — the resource layer never refuses to serve
because one template went stale.

Single-shape reads (``templates://etl/<shape>``) still return ``404`` as
a ``ValueError`` to the FastMCP transport when the file is missing,
because the agent explicitly asked for that shape and a silent empty
body would be misleading.

Directory layout
----------------

The conventions this module enforces:

* Templates live under ``templates/etl/<shape>.yml`` (one file per
  shape, ``.yml`` extension, lower-snake-case shape name).
* The paired sample directory is ``templates/etl/<shape>_sample/`` (any
  subdirectory tree is walked; no nesting depth limit beyond the
  underlying filesystem's).
* Files larger than :data:`_PREVIEW_MAX_BYTES` are still listed but
  their ``preview`` is ``null`` so the manifest stays bounded regardless
  of sample size.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src.pipeline.etl_config import SourceConfig

__all__ = [
    "TEMPLATES_DIR",
    "discover_templates",
    "list_templates_payload",
    "load_template_yaml",
    "load_sample_manifest",
]

_log = logging.getLogger(__name__)

# Repository-root-relative templates directory. The module lives at
# ``src/mcp/resources/etl_templates.py`` (3 levels deep) so the repo root
# is the third ``.parent``.
TEMPLATES_DIR: Path = Path(__file__).resolve().parents[3] / "templates" / "etl"

# Per-shape sample directory suffix. Kept as a module-level constant so
# the convention is visible in one place rather than scattered through
# string concatenations.
_SAMPLE_DIR_SUFFIX = "_sample"

# Number of bytes read from each sample file when building the preview
# field of the sample manifest. 200 was chosen to match the spec text
# in issue #380 — large enough to show the start of a CSV/JSON/SQL
# header, small enough to keep the manifest payload bounded even when
# a sample directory contains many files.
_PREVIEW_MAX_BYTES = 200

# Encodings tried in order when reading a sample file for the preview.
# UTF-8 is the canonical case; latin-1 is the fallback that never raises
# so we can still produce SOMETHING for ASCII-with-stray-bytes files
# without claiming it as authoritative text. Files that can't be decoded
# get a ``null`` preview.
_PREVIEW_ENCODINGS = ("utf-8",)

# Sentinel description text surfaced when neither the YAML field nor the
# README has a usable line. Kept literal (not formatted with the shape
# name) so a quick grep in MCP responses flags missing descriptions.
_NO_DESCRIPTION_PLACEHOLDER = "(no description available)"


def _read_yaml_description(yaml_path: Path) -> str:
    """Return the top-level ``description:`` value from a template YAML.

    Returns an empty string if the file is unparseable, has no
    ``description`` field, or the field is empty after stripping. The
    caller decides whether to fall back to the README.

    Args:
        yaml_path: Absolute path to the template YAML file.

    Returns:
        The trimmed description string, or ``""`` if absent / unusable.
    """
    try:
        with yaml_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        _log.warning(
            "Could not parse template YAML for description: path=%s error=%s",
            yaml_path,
            type(exc).__name__,
        )
        return ""

    if not isinstance(raw, dict):
        return ""

    value = raw.get("description")
    if not isinstance(value, str):
        return ""
    return value.strip()


def _read_readme_description(readme_path: Path) -> str:
    """Return the first prose paragraph after the H1 in a README.

    The README convention used under ``templates/etl/`` is:

    .. code-block:: markdown

       # Shape-name template

       Prose paragraph explaining when to use the template ...

    This helper walks the file line-by-line, skips everything up to and
    including the first H1, skips any blank lines, then accumulates
    non-blank lines until the next blank line or H2. The result is
    stripped and returned.

    Args:
        readme_path: Absolute path to the paired ``<shape>_README.md``.

    Returns:
        The first prose paragraph, trimmed. ``""`` if the README is
        missing, empty, or has no prose after its H1.
    """
    if not readme_path.is_file():
        return ""

    try:
        text = readme_path.read_text(encoding="utf-8")
    except OSError as exc:
        _log.warning(
            "Could not read README for description: path=%s error=%s",
            readme_path,
            type(exc).__name__,
        )
        return ""

    lines = text.splitlines()
    # Phase 1: skip everything up to and including the first H1.
    i = 0
    while i < len(lines) and not lines[i].lstrip().startswith("# "):
        i += 1
    if i >= len(lines):
        return ""
    i += 1  # step past the H1 itself

    # Phase 2: skip blank lines following the H1.
    while i < len(lines) and not lines[i].strip():
        i += 1

    # Phase 3: collect contiguous non-blank lines until the next blank
    # line or new heading.
    paragraph_parts: List[str] = []
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped:
            break
        if stripped.startswith("#"):
            break
        paragraph_parts.append(line)
        i += 1

    return " ".join(part.strip() for part in paragraph_parts).strip()


def _resolve_description(shape: str, yaml_path: Path) -> str:
    """Pick a description for *shape* by walking the priority sources.

    See the module docstring for the priority list. Returns the
    placeholder sentinel if every source is empty so the caller never
    surfaces a blank description to the agent.

    Args:
        shape: Template shape name (the YAML stem).
        yaml_path: Absolute path to the template YAML file.

    Returns:
        A non-empty description string.
    """
    yaml_desc = _read_yaml_description(yaml_path)
    if yaml_desc:
        return yaml_desc

    readme_path = yaml_path.parent / f"{shape}_README.md"
    readme_desc = _read_readme_description(readme_path)
    if readme_desc:
        return readme_desc

    return _NO_DESCRIPTION_PLACEHOLDER


def _validates_through_source_config(yaml_path: Path) -> bool:
    """Return True if the template YAML parses via SourceConfig.

    Mirrors the existing template unit-test contract — every committed
    template must round-trip through
    :meth:`src.pipeline.etl_config.SourceConfig.model_validate`. A
    template that fails this gate is hidden from the MCP resource list
    so an agent never picks up a half-broken shape.

    Failures are logged at WARNING level with the path + exception
    type; the resource handler never raises during discovery.

    Args:
        yaml_path: Absolute path to the template YAML file.

    Returns:
        True if the YAML parsed AND validated; False otherwise.
    """
    try:
        with yaml_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        _log.warning(
            "Skipping ETL template — YAML parse failed: path=%s error=%s",
            yaml_path,
            type(exc).__name__,
        )
        return False

    if not isinstance(raw, dict):
        _log.warning(
            "Skipping ETL template — YAML root is not a mapping: path=%s",
            yaml_path,
        )
        return False

    try:
        SourceConfig.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError + safety net
        _log.warning(
            "Skipping ETL template — SourceConfig validation failed: "
            "path=%s error=%s",
            yaml_path,
            type(exc).__name__,
        )
        return False
    return True


def discover_templates(
    templates_dir: Optional[Path] = None,
) -> List[Dict[str, str]]:
    """Walk *templates_dir* and return ``[{shape, description}]`` entries.

    Each entry has the shape::

        {"shape": "<lower_snake>", "description": "<one-liner>"}

    Templates whose YAML fails to load via
    :meth:`SourceConfig.model_validate` are omitted (and a WARNING is
    logged); see the module docstring for the failure-handling
    contract.

    Args:
        templates_dir: Optional override (used by tests). Defaults to
            :data:`TEMPLATES_DIR`.

    Returns:
        Entries sorted by ``shape`` (deterministic ordering for MCP
        clients; also stable in golden-file diffs).
    """
    base = templates_dir or TEMPLATES_DIR
    if not base.is_dir():
        _log.warning("ETL templates directory not found: %s", base)
        return []

    entries: List[Dict[str, str]] = []
    for yaml_path in sorted(base.glob("*.yml")):
        shape = yaml_path.stem
        if not _validates_through_source_config(yaml_path):
            continue
        entries.append(
            {
                "shape": shape,
                "description": _resolve_description(shape, yaml_path),
            }
        )
    return entries


def list_templates_payload(
    templates_dir: Optional[Path] = None,
) -> List[Dict[str, str]]:
    """Return the payload for ``templates://etl/list``.

    Thin alias over :func:`discover_templates` — kept as its own
    function so the registration layer in :mod:`src.mcp.server` reads
    declaratively (``payload = list_templates_payload()``).

    Args:
        templates_dir: Optional override (used by tests).

    Returns:
        The same list :func:`discover_templates` produces.
    """
    return discover_templates(templates_dir=templates_dir)


def load_template_yaml(
    shape: str,
    templates_dir: Optional[Path] = None,
) -> str:
    """Return the raw YAML body for ``templates://etl/<shape>``.

    The file content is returned **verbatim** — comments, indentation,
    and ``<FILL_IN_*>`` placeholders are preserved so the agent can
    surface the template to the user as a copy-pasteable starting
    point. No transformation is applied.

    Args:
        shape: Template shape (the YAML stem, e.g. ``csv_file_comparison``).
        templates_dir: Optional override (used by tests).

    Returns:
        UTF-8 text content of the template file.

    Raises:
        ValueError: If *shape* is not a discovered template (either the
            file does not exist, or it fails ``SourceConfig`` validation
            and would not appear in ``templates://etl/list``).
    """
    base = templates_dir or TEMPLATES_DIR
    yaml_path = base / f"{shape}.yml"
    if not yaml_path.is_file():
        raise ValueError(
            f"Unknown ETL template shape: {shape!r}. "
            "Use templates://etl/list to discover available shapes."
        )
    if not _validates_through_source_config(yaml_path):
        # The template exists but is currently broken; surface that
        # explicitly so the agent doesn't paste a half-broken file into
        # a working spec.
        raise ValueError(
            f"ETL template {shape!r} failed SourceConfig validation; "
            "see server logs for details."
        )
    return yaml_path.read_text(encoding="utf-8")


def _read_preview(
    file_path: Path,
    max_bytes: int = _PREVIEW_MAX_BYTES,
) -> Optional[str]:
    """Return up to *max_bytes* of *file_path* as text, or ``None``.

    Returns ``None`` when the file cannot be decoded as text under any
    of :data:`_PREVIEW_ENCODINGS`, signalling to the agent that the
    file is binary and the preview should be omitted rather than
    forced. Decode errors are NOT logged at WARNING — a binary file in
    a sample directory is a routine occurrence (e.g. a ``.xlsx`` or
    ``.zip`` fixture), not an operational problem.

    Args:
        file_path: Absolute path to the sample file.
        max_bytes: Hard cap on how many bytes are read from the file.
            The caller may shrink this for very large fixtures.

    Returns:
        The decoded text preview, or ``None`` for binary content / read
        failures.
    """
    try:
        with file_path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)  # +1 so callers can tell if truncated
    except OSError:
        return None
    if not raw:
        return ""
    # Treat the file as binary if it contains a NUL byte in the prefix.
    # This is the same heuristic ``git diff`` uses to decide whether to
    # show "Binary files differ".
    if b"\x00" in raw:
        return None
    for encoding in _PREVIEW_ENCODINGS:
        try:
            decoded = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        return decoded[:max_bytes]
    return None


def load_sample_manifest(
    shape: str,
    templates_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Return the payload for ``templates://etl/<shape>/sample``.

    Walks ``<shape>_sample/`` recursively and returns a manifest of
    every contained file. The manifest is intentionally a *manifest*,
    not a tarball — large fixtures (Excel workbooks, multi-MB CSVs)
    would otherwise blow the MCP response budget for no good reason.

    The returned shape::

        {
            "sample_dir": "templates/etl/<shape>_sample",
            "files": [
                {
                    "path": "<rel-path>",
                    "size_bytes": <int>,
                    "preview": "<first 200 chars>" | null,
                },
                ...
            ],
        }

    File entries are sorted by relative path for deterministic output
    (stable across processes; diff-friendly).

    Args:
        shape: Template shape whose paired sample directory should be
            manifested.
        templates_dir: Optional override (used by tests).

    Returns:
        The manifest dictionary described above.

    Raises:
        ValueError: If either the template OR the paired sample
            directory does not exist. The two failure modes share an
            exception type so the FastMCP transport surfaces the same
            ``ValueError`` shape to clients.
    """
    base = templates_dir or TEMPLATES_DIR
    yaml_path = base / f"{shape}.yml"
    if not yaml_path.is_file():
        raise ValueError(
            f"Unknown ETL template shape: {shape!r}. "
            "Use templates://etl/list to discover available shapes."
        )

    sample_dir = base / f"{shape}{_SAMPLE_DIR_SUFFIX}"
    if not sample_dir.is_dir():
        raise ValueError(
            f"ETL template {shape!r} has no paired sample directory at "
            f"{sample_dir.relative_to(base.parent.parent)}."
        )

    files_payload: List[Dict[str, Any]] = []
    for file_path in sorted(p for p in sample_dir.rglob("*") if p.is_file()):
        try:
            size_bytes = file_path.stat().st_size
        except OSError:
            # Race against a concurrent delete — skip silently.
            continue
        files_payload.append(
            {
                "path": str(file_path.relative_to(sample_dir)),
                "size_bytes": size_bytes,
                "preview": _read_preview(file_path),
            }
        )

    # Use a repo-root-relative path so the manifest is portable across
    # checkouts. ``base.parent.parent`` is the repo root because
    # templates_dir == <repo>/templates/etl.
    repo_root = base.parent.parent
    return {
        "sample_dir": str(sample_dir.relative_to(repo_root)),
        "files": files_payload,
    }
