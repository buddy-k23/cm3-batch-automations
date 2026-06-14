"""Mapping management endpoints."""

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query
from typing import List
import sys
from pathlib import Path
import json
from datetime import datetime
import shutil

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.api.auth import require_role
from src.api.models.mapping import (
    MappingCreate,
    MappingResponse,
    MappingListItem,
    ValidationResult,
    UploadResponse
)
from src.config.template_converter import TemplateConverter
from src.config.universal_mapping_parser import UniversalMappingParser

router = APIRouter()

# Mapping storage directory
MAPPINGS_DIR = Path("config/mappings")
MAPPINGS_DIR.mkdir(parents=True, exist_ok=True)

UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/upload", response_model=UploadResponse)
async def upload_template(
    _=Depends(require_role("mapping_owner")),
    file: UploadFile = File(...),
    mapping_name: str = Query(None, description="Name for the mapping"),
    file_format: str = Query(None, description="File format: fixed_width, pipe_delimited, csv, tsv")
):
    """
    Upload Excel/CSV template and convert to universal mapping.
    
    - **file**: Excel (.xlsx, .xls) or CSV file
    - **mapping_name**: Optional name for the mapping (defaults to filename)
    - **file_format**: Optional format specification
    
    Returns the created mapping ID and details.
    """
    # Validate file extension
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Only .xlsx, .xls, and .csv files are supported."
        )
    
    # Save uploaded file
    upload_path = UPLOADS_DIR / file.filename
    with open(upload_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        # Convert template to mapping
        converter = TemplateConverter()
        
        if file.filename.endswith(('.xlsx', '.xls')):
            mapping = converter.from_excel(
                str(upload_path),
                mapping_name=mapping_name,
                file_format=file_format
            )
        else:
            mapping = converter.from_csv(
                str(upload_path),
                mapping_name=mapping_name,
                file_format=file_format
            )
        
        # Save mapping
        mapping_id = mapping['mapping_name']
        output_path = MAPPINGS_DIR / f"{mapping_id}.json"
        converter.save(str(output_path))
        
        # Read back the generated JSON for preview
        mapping_content = None
        if output_path.exists():
            import json as _json
            with open(output_path) as _f:
                mapping_content = _json.load(_f)

        conversion_warnings = mapping.get("warnings", [])
        message = f"Template converted successfully. Mapping saved as '{mapping_id}'"
        if conversion_warnings:
            message += f". {len(conversion_warnings)} warning(s) — see 'warnings' field."

        response = {
            "filename": file.filename,
            "size": upload_path.stat().st_size,
            "mapping_id": mapping_id,
            "message": message,
            "mapping_content": mapping_content,
            "warnings": conversion_warnings,
        }
        return response
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error converting template: {str(e)}")
    
    finally:
        # Clean up uploaded file
        if upload_path.exists():
            upload_path.unlink()


@router.get("/", response_model=List[MappingListItem])
async def list_mappings():
    """
    List all available mappings.
    
    Returns a list of all mappings with basic information.
    """
    mappings = []
    
    for mapping_file in MAPPINGS_DIR.glob("*.json"):
        try:
            with open(mapping_file, 'r') as f:
                mapping = json.load(f)
            
            mappings.append(MappingListItem(
                id=mapping['mapping_name'],
                mapping_name=mapping['mapping_name'],
                version=mapping.get('version', '1.0.0'),
                format=mapping['source']['format'],
                total_fields=len(mapping['fields']),
                created_date=mapping.get('metadata', {}).get('created_date', 'Unknown')
            ))
        except Exception:
            continue
    
    return mappings


@router.get("/{mapping_id}", response_model=MappingResponse)
async def get_mapping(mapping_id: str):
    """
    Get mapping by ID.
    
    Returns the complete mapping configuration.
    """
    mapping_file = MAPPINGS_DIR / f"{mapping_id}.json"
    
    if not mapping_file.exists():
        raise HTTPException(status_code=404, detail=f"Mapping '{mapping_id}' not found")
    
    try:
        parser = UniversalMappingParser(mapping_path=str(mapping_file))
        mapping = parser.to_dict()
        
        return MappingResponse(
            id=mapping_id,
            mapping_name=mapping['mapping_name'],
            version=mapping.get('version', '1.0.0'),
            description=mapping.get('description'),
            source=mapping['source'],
            target=mapping.get('target'),
            fields=mapping['fields'],
            key_columns=mapping.get('key_columns', []),
            metadata=mapping.get('metadata'),
            total_fields=len(mapping['fields']),
            total_record_length=mapping.get('total_record_length')
        )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading mapping: {str(e)}")


@router.post("/validate", response_model=ValidationResult)
async def validate_mapping(mapping: MappingCreate):
    """
    Validate mapping structure.
    
    Validates the mapping against the schema without saving it.
    """
    try:
        # Convert to dict and validate
        mapping_dict = mapping.model_dump()
        parser = UniversalMappingParser(mapping_dict=mapping_dict)
        validation = parser.validate_schema()
        
        return ValidationResult(
            valid=validation['valid'],
            errors=validation['errors'],
            warnings=validation['warnings']
        )
    
    except Exception as e:
        return ValidationResult(
            valid=False,
            errors=[str(e)],
            warnings=[]
        )


@router.delete("/{mapping_id}")
async def delete_mapping(mapping_id: str, _=Depends(require_role("mapping_owner"))):
    """
    Delete mapping by ID.
    
    Permanently removes the mapping file.
    """
    mapping_file = MAPPINGS_DIR / f"{mapping_id}.json"
    
    if not mapping_file.exists():
        raise HTTPException(status_code=404, detail=f"Mapping '{mapping_id}' not found")
    
    try:
        mapping_file.unlink()
        return {"success": True, "message": f"Mapping '{mapping_id}' deleted successfully"}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting mapping: {str(e)}")
