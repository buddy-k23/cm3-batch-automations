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


__all__ = ["EmitterError", "derive_layout_tag"]
