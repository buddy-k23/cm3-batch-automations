"""Multi-record config generation endpoint.

Exposes ``POST /api/v1/multi-record/generate`` which accepts a JSON body
describing the multi-record structure and returns a YAML file as a download.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from src.config.multi_record_config import MultiRecordConfig

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
