"""Source-onboarding emitters (EC-S3 / EC-S4 / EC-S5).

This package owns the translation from a parsed
:class:`~src.onboarding.models.OnboardingWorkbook` (produced by the EC-S2
reader) into the on-disk artefacts the engine consumes:

    * EC-S3 -- :mod:`src.onboarding.emitters.source_yaml_emitter` writes the
      per-source YAML under ``config/e2e/sources/<SOURCE>.yml``.
    * EC-S4 -- :mod:`src.onboarding.emitters.mapping_emitter` writes the
      per-file mapping JSONs (and the per-multi-record umbrella YAMLs)
      under ``config/mappings/``.
    * EC-S5 -- :mod:`src.onboarding.emitters.rules_emitter` writes the
      per-file rules JSONs under ``config/rules/``.

All emitters share a common failure mode: when the synthesised artefact
fails downstream validation (e.g. Pydantic ``SourceConfig`` rejects the
emitted YAML), an :class:`EmitterError` is raised with the validation
error chained as ``__cause__`` so the BA sees the model-level message.

The :func:`derive_layout_tag` helper is shared between the EC-S4 mapping
emitter and the EC-S5 rules emitter so that two record-types sharing a
mapping layout (e.g. SHAW TRANERT ``rt_32000`` + ``rt_32001`` both
pointing at ``TRANERT_NEW1_Mapping`` / ``TRANERT_NEW1_Rules``) collapse
to a single emitted JSON for BOTH the mapping and rules emitters.
"""

from __future__ import annotations


class EmitterError(Exception):
    """Raised by an EC-S3/S4/S5 emitter when the synthesised artefact
    fails its downstream validation contract.

    Distinct from ``WorkbookSchemaError`` (EC-S1) and ``WorkbookReadError``
    (EC-S2):

        * ``WorkbookSchemaError`` -- workbook *shape* problems.
        * ``WorkbookReadError``   -- workbook *value* problems.
        * ``EmitterError``        -- the parsed workbook produced output
          that the downstream model rejects (e.g. an empty
          ``source_code`` propagated into ``SourceConfig.source``).

    The original validation exception is chained as ``__cause__`` so the
    BA sees the underlying Pydantic message without losing the emitter-
    layer context.
    """


def derive_layout_tag(file_type: str, sheet_name: str, *, suffix: str) -> str:
    """Derive the layout tag used in per-record-type filenames.

    The per-record-type mapping JSON (EC-S4) and rules JSON (EC-S5) are
    named after the LAYOUT (the mapping/rules sheet), not after the
    discriminator value. This matches the committed convention where
    ``rt_32000`` and ``rt_32001`` (two discriminator values) both
    reference the single ``SHAW_TRANERT_NEW1_mapping.json`` (one layout)
    and the single ``SHAW_TRANERT_NEW1_rules.json``.

    Algorithm:
        Strip the ``<FILE_TYPE>_`` prefix and the configurable ``suffix``
        (``"_Mapping"`` for EC-S4, ``"_Rules"`` for EC-S5). Anything
        between is the layout tag.

    Tilde-shortened tabs (``CDSTRANS_EF~AIVERS_Mapping``) yield the
    shortened layout tag (``EF~AIVERS``). The helper does not attempt
    to "unshorten" -- the layout tag becomes part of the filename and
    the BA can rename if needed.

    Args:
        file_type: The output-file file type (e.g. ``"TRANERT"``).
        sheet_name: The mapping/rules sheet name
            (e.g. ``"TRANERT_NEW1_Mapping"`` or ``"TRANERT_NEW1_Rules"``).
        suffix: ``"_Mapping"`` for EC-S4, ``"_Rules"`` for EC-S5.

    Returns:
        The layout tag (e.g. ``"NEW1"``).

    Raises:
        EmitterError: When the sheet name does not follow the
            ``<FILE_TYPE>_<LAYOUT><suffix>`` convention.
    """
    expected_prefix = f"{file_type}_"
    if not (sheet_name.startswith(expected_prefix) and sheet_name.endswith(suffix)):
        raise EmitterError(
            f"Multi-record sheet '{sheet_name}' does not follow the "
            f"'{expected_prefix}<LAYOUT>{suffix}' convention; "
            f"cannot derive per-record-type filename."
        )
    return sheet_name[len(expected_prefix) : -len(suffix)]


def derive_rules_artefact_path(
    source_code: str,
    file_type: str,
    rules_sheet_name: str,
    *,
    rules_dir: str = "config/rules",
) -> str | None:
    """Compute the canonical rules-artefact path for one workbook row.

    Shared helper called by BOTH emitters so EC-S4's umbrella YAML and
    EC-S5's rules JSON emission stay perfectly in sync on the
    rules-artefact filename. Resolves the EC-S7 drift category where
    the umbrella YAML carried ``rules: ""`` while EC-S5 was actually
    emitting a real per-record-type rules JSON at the layout-tagged
    path.

    Path-derivation rules:

        * ``rules_sheet_name == ""`` (BA explicitly omitted rules) ->
          returns ``None`` so the caller can omit the ``rules`` key
          entirely (EC-S7 acceptance criterion #1) rather than emit
          ``""``.
        * ``rules_sheet_name == "(umbrella)"`` -> returns ``None``.
          The umbrella token never resolves to a rules artefact: it
          is the sentinel that says "see the per-record-type rows in
          the MultiRecord_<FT> sheet". Each row carries its own
          ``rules_sheet`` which this helper resolves separately.
        * Otherwise: when the sheet name matches the
          ``<FILE_TYPE>_<LAYOUT>_Rules`` convention, returns
          ``<rules_dir>/<SOURCE>_<FT>_<LAYOUT>_rules.json``. When it
          does not (the FLAT-output convention where
          ``OutputFileSpec.rules_sheet`` is e.g. ``CDSTRANS_EFB_Rules``
          but there is only one rules layout per flat file), returns
          ``<rules_dir>/<SOURCE>_<FT>.json``. The fallback is taken
          ONLY when the layout-tag derivation raises
          :class:`EmitterError` -- i.e. the convention check is the
          discriminator between multi-record (layout-tagged) and flat
          (no layout tag) rules paths.

    Args:
        source_code: The source code (e.g. ``"SHAW"``). Becomes the
            filename prefix.
        file_type: The output-file file type (e.g. ``"TRANERT"``).
            Becomes the second filename component.
        rules_sheet_name: The ``rules_sheet`` cell value from either
            an :class:`~src.onboarding.models.OutputFileSpec` (flat
            output) or a :class:`~src.onboarding.models.MultiRecordRow`
            (multi-record per-type).
        rules_dir: Directory prefix for the emitted artefact. Defaults
            to ``"config/rules"`` to match the committed convention;
            tests may override.

    Returns:
        The canonical rules-artefact path (e.g.
        ``"config/rules/SHAW_TRANERT_NEW1_rules.json"`` or
        ``"config/rules/SHAW_CDSTRANS_EFB.json"``), or ``None`` when
        no rules artefact should be emitted (blank or umbrella token).

    Examples:
        >>> derive_rules_artefact_path("SHAW", "TRANERT", "TRANERT_NEW1_Rules")
        'config/rules/SHAW_TRANERT_NEW1_rules.json'
        >>> derive_rules_artefact_path("SHAW", "CDSTRANS_EFB", "CDSTRANS_EFB_Rules")
        'config/rules/SHAW_CDSTRANS_EFB.json'
        >>> derive_rules_artefact_path("SHAW", "TRANERT", "") is None
        True
        >>> derive_rules_artefact_path("SHAW", "TRANERT", "(umbrella)") is None
        True
    """
    if not rules_sheet_name:
        return None
    if rules_sheet_name == "(umbrella)":
        return None

    rules_dir = rules_dir.rstrip("/")

    # Decide between two filename shapes by inspecting the sheet name:
    #
    #   * Multi-record layout convention:
    #       ``<FILE_TYPE>_<LAYOUT>_Rules`` (non-empty <LAYOUT>)
    #       -> ``<rules_dir>/<SOURCE>_<FT>_<LAYOUT>_rules.json``
    #   * Flat-output convention:
    #       ``<FILE_TYPE>_Rules``           (no <LAYOUT> segment)
    #       -> ``<rules_dir>/<SOURCE>_<FT>.json``
    #
    # ``derive_layout_tag`` returns the empty string for the flat shape
    # (it succeeds because the prefix + suffix consume the whole sheet
    # name with nothing in between) and raises ``EmitterError`` only
    # when the sheet name does not conform at all. The empty-tag case
    # is the discriminator we use to pick the flat path.
    try:
        layout_tag = derive_layout_tag(
            file_type, rules_sheet_name, suffix="_Rules"
        )
    except EmitterError:
        # Sheet name does not conform to either convention -- re-raise
        # so the caller can surface the BA error (vs. silently emitting
        # an unsupported path).
        raise

    if layout_tag == "":
        # Flat-output convention: ``<FT>_Rules`` -> ``<SOURCE>_<FT>.json``.
        return f"{rules_dir}/{source_code}_{file_type}.json"
    return f"{rules_dir}/{source_code}_{file_type}_{layout_tag}_rules.json"


__all__ = ["EmitterError", "derive_layout_tag", "derive_rules_artefact_path"]
