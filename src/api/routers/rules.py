"""Rules upload and download endpoints."""

import shutil
import sys
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.api.auth import require_role
from src.config.ba_rules_template_converter import BARulesTemplateConverter
from src.config.rules_template_converter import RulesTemplateConverter

router = APIRouter()

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
RULES_DIR = _REPO_ROOT / "config" / "rules"
RULES_DIR.mkdir(parents=True, exist_ok=True)

UPLOADS_DIR = _REPO_ROOT / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/")
async def list_rules():
    """List all available rules configurations."""
    import json
    rules = []
    for f in sorted(RULES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            meta = data.get("metadata", {})
            rules.append({
                "id": f.stem,
                "name": meta.get("name", f.stem),
                "filename": f.name,
                "rule_count": len(data.get("rules", [])),
            })
        except Exception:
            rules.append({"id": f.stem, "name": f.stem, "filename": f.name, "rule_count": 0})
    return rules


@router.post("/upload")
async def upload_rules_template(
    _=Depends(require_role("mapping_owner")),
    file: UploadFile = File(...),
    rules_name: str = Query(None, description="Name for the rules config"),
    rules_type: Literal["ba_friendly", "technical"] = Query("ba_friendly", description="ba_friendly or technical"),
):
    """Upload Excel/CSV rules template and convert to rules JSON.

    Args:
        file: The uploaded template file (.xlsx, .xls, or .csv).
        rules_name: Optional name for the output rules config.
            Defaults to the uploaded filename stem.
        rules_type: Converter to use — ``ba_friendly`` (default) or ``technical``.

    Returns:
        Dict with keys: ``rules_id``, ``filename``, ``size``, ``message``.

    Raises:
        HTTPException: 400 if file extension is not .xlsx, .xls, or .csv.
        HTTPException: 500 if template conversion fails.
    """
    if not file.filename.endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(status_code=400, detail="Only .xlsx, .xls, and .csv files are supported.")

    upload_path = UPLOADS_DIR / file.filename
    with open(upload_path, "wb") as buf:
        shutil.copyfileobj(file.file, buf)

    try:
        if rules_type == "technical":
            converter = RulesTemplateConverter()
        else:
            converter = BARulesTemplateConverter()

        if file.filename.endswith((".xlsx", ".xls")):
            converter.from_excel(str(upload_path))
        else:
            converter.from_csv(str(upload_path))

        rules_id = rules_name or Path(file.filename).stem
        if converter.rules_config:
            converter.rules_config.setdefault("metadata", {})["name"] = rules_id

        output_path = RULES_DIR / f"{rules_id}.json"
        converter.save(str(output_path))

        # Read back generated JSON for preview
        rules_content = None
        if output_path.exists():
            import json as _json
            with open(output_path) as _f:
                rules_content = _json.load(_f)

        return {
            "rules_id": rules_id,
            "filename": file.filename,
            "size": upload_path.stat().st_size,
            "message": f"Rules template converted and created successfully. Rules saved as '{rules_id}'",
            "rules_content": rules_content,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error converting rules template: {str(e)}")

    finally:
        if upload_path.exists():
            upload_path.unlink()


@router.get("/{rules_id}.json")
async def download_rules(rules_id: str):
    """Download a generated rules JSON file by ID.

    Args:
        rules_id: The rules config identifier (without .json extension).

    Returns:
        The rules JSON file as a downloadable attachment.

    Raises:
        HTTPException: 404 if no rules file with the given ID exists.
    """
    path = RULES_DIR / f"{rules_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Rules '{rules_id}' not found")
    return FileResponse(str(path), media_type="application/json", filename=f"{rules_id}.json")
