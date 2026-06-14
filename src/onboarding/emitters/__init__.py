"""Source-onboarding emitters (EC-S3 / EC-S4 / EC-S5).

This package owns the translation from a parsed
:class:`~src.onboarding.models.OnboardingWorkbook` (produced by the EC-S2
reader) into the on-disk artefacts the engine consumes:

    * EC-S3 — :mod:`src.onboarding.emitters.source_yaml_emitter` writes the
      per-source YAML under ``config/e2e/sources/<SOURCE>.yml``.
    * EC-S4 — (future) per-file mapping JSON under ``config/mappings/``.
    * EC-S5 — (future) per-file rules JSON under ``config/rules/``.

All emitters share a common failure mode: when the synthesised artefact
fails downstream validation (e.g. Pydantic ``SourceConfig`` rejects the
emitted YAML), an :class:`EmitterError` is raised with the validation
error chained as ``__cause__`` so the BA sees the model-level message.
"""

from __future__ import annotations


class EmitterError(Exception):
    """Raised by an EC-S3/S4/S5 emitter when the synthesised artefact
    fails its downstream validation contract.

    Distinct from ``WorkbookSchemaError`` (EC-S1) and ``WorkbookReadError``
    (EC-S2):

        * ``WorkbookSchemaError`` — workbook *shape* problems.
        * ``WorkbookReadError``   — workbook *value* problems.
        * ``EmitterError``        — the parsed workbook produced output
          that the downstream model rejects (e.g. an empty
          ``source_code`` propagated into ``SourceConfig.source``).

    The original validation exception is chained as ``__cause__`` so the
    BA sees the underlying Pydantic message without losing the emitter-
    layer context.
    """


__all__ = ["EmitterError"]
