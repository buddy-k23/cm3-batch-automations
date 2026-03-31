"""Multi-record config generation and wizard-helper endpoints.

Exposes:
- ``POST /api/v1/multi-record/generate`` — generate a YAML config from JSON body.
- ``POST /api/v1/multi-record/detect-discriminator`` — auto-detect the
  discriminator field from an uploaded batch file.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, UploadFile
from fastapi.responses import Response

from src.config.multi_record_config import MultiRecordConfig
from src.services.multi_record_wizard_service import detect_discriminator

router = APIRouter()


@router.post(
    "/generate",
    summary="Generate a multi-record YAML config file",
    response_description="YAML file as a downloadable attachment",
)
async def generate_multi_record_config(config: MultiRecordConfig) -> Response:
    """Accept a multi-record config JSON body and return it as a YAML file download.

    The returned file can be saved and used directly with the
    ``validate --multi-record`` CLI option.

    Args:
        config: :class:`~src.config.multi_record_config.MultiRecordConfig` describing
            the discriminator, record types, and optional cross-type rules.

    Returns:
        A ``application/x-yaml`` response containing the serialized YAML config,
        with ``Content-Disposition: attachment; filename="multi_record_config.yaml"``.

    Example request body::

        {
          "discriminator": {"field": "REC_TYPE", "position": 1, "length": 3},
          "record_types": {
            "header":  {"match": "HDR", "mapping": "config/mappings/hdr.json"},
            "detail":  {"match": "DTL", "mapping": "config/mappings/dtl.json"},
            "trailer": {"match": "TRL", "mapping": "config/mappings/trl.json", "expect": "exactly_one"}
          },
          "cross_type_rules": [
            {"check": "required_companion", "when_type": "header", "requires_type": "detail"},
            {"check": "header_trailer_count", "record_type": "trailer", "trailer_field": "RECORD_COUNT", "count_of": "detail"}
          ],
          "default_action": "warn"
        }
    """
    import yaml

    # Serialise via Pydantic's model_dump then to YAML.
    data = config.model_dump(exclude_none=False)

    # Convert record_types dicts: drop empty strings for cleaner YAML.
    record_types_clean = {}
    for key, val in data.get("record_types", {}).items():
        if isinstance(val, dict):
            record_types_clean[key] = {k: v for k, v in val.items() if v != ""}
        else:
            record_types_clean[key] = val
    data["record_types"] = record_types_clean

    # Drop empty cross_type_rules list for conciseness.
    if not data.get("cross_type_rules"):
        data.pop("cross_type_rules", None)

    yaml_content = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return Response(
        content=yaml_content,
        media_type="application/x-yaml",
        headers={
            "Content-Disposition": 'attachment; filename="multi_record_config.yaml"'
        },
    )


@router.post(
    "/detect-discriminator",
    summary="Auto-detect the discriminator field from a batch file sample",
    response_description="Candidate discriminator positions with confidence scores",
)
async def detect_discriminator_endpoint(
    file: UploadFile,
    max_lines: int = Query(default=20, ge=1, le=1000),
) -> dict:
    """Scan the uploaded file and return likely discriminator field candidates.

    Reads up to *max_lines* lines and scores each ``(position, length)`` pair
    by how consistently it produces 2–8 repeating distinct values.  The
    ``best`` key contains the highest-confidence candidate.

    Args:
        file: Batch file to scan (any text format).
        max_lines: Number of lines to inspect.  Defaults to ``20``.

    Returns:
        Dict with keys:

        - ``candidates``: list of dicts — ``position`` (1-indexed), ``length``,
          ``values`` (list of distinct values found), ``confidence`` (0–1).
          Sorted descending by confidence.
        - ``best``: the highest-confidence candidate dict, or ``null`` when
          none found.

    Example response::

        {
          "candidates": [
            {"position": 1, "length": 3, "values": ["HDR", "DTL", "TRL"],
             "confidence": 0.95}
          ],
          "best": {"position": 1, "length": 3, "values": ["HDR", "DTL", "TRL"],
                   "confidence": 0.95}
        }
    """
    raw = await file.read()
    content = raw.decode("utf-8", errors="replace")
    return detect_discriminator(content, max_lines=max_lines)
