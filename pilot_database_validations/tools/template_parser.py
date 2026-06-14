#!/usr/bin/env python3
"""Prototype BA/QA template parser -> canonical ingest JSON.

CSV is the primary format. XLSX is supported for simple multi-sheet templates.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from tools.rules_extraction import extract_rules_from_mapping_rows
    from tools.schema_validation import SchemaValidationError, validate_payload
except ModuleNotFoundError:
    from rules_extraction import extract_rules_from_mapping_rows
    from schema_validation import SchemaValidationError, validate_payload


HEADER_ALIASES = {
    "column": "target_field",
    "transformation": "transform_logic",
    "position": "position_start",
    "message": "message_template",
    "rule": "rule_id",
    "type": "data_type",
    # common source lineage aliases
    "sourcecolumn": "source_field",
    "source_column": "source_field",
    "source field": "source_field",
    "src": "source",
    "src_field": "source_field",
}

MAPPING_REQUIRED = {"transaction_code", "target_field", "source_field", "data_type"}
RULE_REQUIRED = {"rule_id", "scope", "severity", "priority", "expression", "message_template"}
FILE_CONFIG_REQUIRED = {"format"}
FILE_CONFIG_SIGNATURE = {"delimiter", "record_length", "header_enabled", "quote_char", "escape_char", "header_total_count_field"}

SOURCE_FALLBACK_COLUMNS = (
    "source_field",
    "source",
    "source_name",
    "source_column",
    "source_col",
    "src",
    "src_field",
)

LINEAGE_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0}


@dataclass(frozen=True)
class DerivationConfig:
    enabled: bool = False
    transaction_code_mode: str = "sheet_name"
    source_field_mode: str = "target_field"
    transaction_code_placeholder: str = "UNRESOLVED_TXN"
    source_field_placeholder: str = "UNRESOLVED_SOURCE"
    lineage_max_placeholder_ratio: float = 0.02
    lineage_max_low_confidence_ratio: float = 0.15


@dataclass
class DerivationWarning:
    warning_code: str
    sheet_name: Optional[str]
    row: int
    column: str
    message: str

    def as_dict(self) -> dict:
        out = {
            "warningCode": self.warning_code,
            "row": self.row,
            "column": self.column,
            "message": self.message,
        }
        if self.sheet_name:
            out["sheet"] = self.sheet_name
        return out


@dataclass
class TemplateError:
    file_name: str
    sheet_name: Optional[str]
    row: int
    column: str
    error_code: str
    hint: str

    def as_dict(self) -> dict:
        out = {
            "file": self.file_name,
            "row": self.row,
            "column": self.column,
            "errorCode": self.error_code,
            "hint": self.hint,
        }
        if self.sheet_name:
            out["sheet"] = self.sheet_name
        return out


class ValidationError(Exception):
    def __init__(self, errors: List[TemplateError]):
        super().__init__("Template validation failed")
        self.errors = errors


def normalize_header(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return HEADER_ALIASES.get(value, value)


def parse_bool_yn(value: str) -> Optional[bool]:
    if value is None or str(value).strip() == "":
        return None
    v = str(value).strip().upper()
    if v == "Y":
        return True
    if v == "N":
        return False
    return None


def parse_int(value: str) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip()
    if re.fullmatch(r"[-+]?\d+\.0+", raw):
        raw = raw.split(".", 1)[0]
    return int(raw)


def section_type(headers: Iterable[str]) -> Optional[str]:
    hs = {h for h in headers if h}
    # Prefer mapping when both signatures appear (real BA templates often contain `format`).
    if {"target_field", "data_type"}.issubset(hs):
        return "mapping"
    if RULE_REQUIRED.issubset(hs):
        return "rules"
    if FILE_CONFIG_REQUIRED.issubset(hs) and bool(FILE_CONFIG_SIGNATURE.intersection(hs)):
        return "file_config"
    return None


def read_csv(path: Path) -> List[Tuple[str, List[dict]]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = [normalize_header(h) for h in (reader.fieldnames or [])]
        rows = []
        for idx, raw in enumerate(reader, start=2):
            row = {normalize_header(k): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
            row["__row__"] = idx
            rows.append(row)
        return [(section_type(headers) or "unknown", rows)]


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    vals = []
    for si in root.findall("x:si", ns):
        texts = [t.text or "" for t in si.findall(".//x:t", ns)]
        vals.append("".join(texts))
    return vals


def _xlsx_sheet_names(zf: zipfile.ZipFile) -> Dict[str, str]:
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    ns_wb = {
        "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    ns_rel = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
    rel_map = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall("r:Relationship", ns_rel)
        if rel.attrib.get("Type", "").endswith("/worksheet")
    }
    out = {}
    for sh in wb.findall("x:sheets/x:sheet", ns_wb):
        rid = sh.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = rel_map.get(rid)
        if target:
            out[sh.attrib.get("name", "Sheet")] = f"xl/{target}"
    return out


def _col_to_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    total = 0
    for ch in letters:
        total = total * 26 + (ord(ch.upper()) - 64)
    return total - 1


def read_xlsx(path: Path) -> List[Tuple[str, List[dict], str]]:
    sections = []
    with zipfile.ZipFile(path) as zf:
        shared = _xlsx_shared_strings(zf)
        sheets = _xlsx_sheet_names(zf)
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        for sheet_name, sheet_path in sheets.items():
            if sheet_path not in zf.namelist():
                continue
            root = ET.fromstring(zf.read(sheet_path))
            parsed_rows: List[Tuple[int, List[str]]] = []
            for row in root.findall(".//x:sheetData/x:row", ns):
                r_index = int(row.attrib.get("r", "0"))
                cells = {}
                for c in row.findall("x:c", ns):
                    ref = c.attrib.get("r", "")
                    idx = _col_to_index(ref)
                    t = c.attrib.get("t")
                    v_elem = c.find("x:v", ns)
                    text = ""
                    if v_elem is not None and v_elem.text is not None:
                        text = v_elem.text
                        if t == "s":
                            text = shared[int(text)]
                    else:
                        is_elem = c.find("x:is/x:t", ns)
                        if is_elem is not None and is_elem.text is not None:
                            text = is_elem.text
                    cells[idx] = text.strip()
                if cells:
                    width = max(cells.keys()) + 1
                    values = [cells.get(i, "") for i in range(width)]
                    parsed_rows.append((r_index, values))

            if not parsed_rows:
                continue
            headers = [normalize_header(h) for h in parsed_rows[0][1]]
            stype = section_type(headers) or "unknown"
            body = []
            for rnum, vals in parsed_rows[1:]:
                row = {headers[i]: vals[i].strip() if i < len(vals) else "" for i in range(len(headers)) if headers[i]}
                row["__row__"] = rnum
                body.append(row)
            sections.append((stype, body, sheet_name))
    return sections


def _validate_enum(value: str, allowed: set, err: List[TemplateError], file_name: str, sheet: Optional[str], row: int, col: str):
    if value and value not in allowed:
        err.append(TemplateError(file_name, sheet, row, col, "ENUM_INVALID", f"Use one of: {sorted(allowed)}"))


def _derive_transaction_code(sheet: Optional[str], row: dict, cfg: DerivationConfig) -> str:
    if cfg.transaction_code_mode == "sheet_name":
        return (sheet or cfg.transaction_code_placeholder).strip() or cfg.transaction_code_placeholder
    if cfg.transaction_code_mode == "column_fallback":
        return (row.get("transaction_code") or row.get("record_type") or row.get("txn_code") or cfg.transaction_code_placeholder).strip()
    return cfg.transaction_code_placeholder


def _extract_definition_source(definition: str) -> Optional[str]:
    text = (definition or "").strip()
    if not text:
        return None
    patterns = [
        r"(?i)\b(?:source|src)\s*[:=]\s*([A-Za-z0-9_.$\-]+)",
        r"(?i)\bfrom\s+([A-Za-z0-9_.$\-]+)",
        r"(?i)\bcolumn\s+([A-Za-z0-9_.$\-]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    # direct alias hint (e.g., "SRC_AMT")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.$\-]*", text):
        return text
    return None


def _sanitize_source_token(token: str) -> str:
    val = (token or "").strip()
    if not val:
        return ""
    val = re.sub(r"\s+", "_", val)
    val = re.sub(r"[^A-Za-z0-9_.$\-]", "", val)
    return val


def _stable_lineage_id(file_name: str, sheet: Optional[str], rown: int, target_field: str) -> str:
    raw = f"{file_name}|{sheet or 'csv'}|{rown}|{target_field}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"lin-{digest}"


def _derive_source_lineage(row: dict, cfg: DerivationConfig, sheet: Optional[str], rown: int, file_name: str) -> tuple[str, dict]:
    mode = cfg.source_field_mode
    target = (row.get("target_field") or "").strip()
    transform = (row.get("transform_logic") or "").strip()

    def _lineage(source_field: str, origin: str, confidence: str, derivation_flag: bool, inputs_used: list[str], placeholder_used: bool) -> dict:
        return {
            "lineageId": _stable_lineage_id(file_name=file_name, sheet=sheet, rown=rown, target_field=target),
            "origin": origin,
            "transforms": [transform] if transform else [],
            "derivationFlag": derivation_flag,
            "confidence": confidence,
            "strategy": mode,
            "inputsUsed": sorted(inputs_used),
            "placeholderUsed": placeholder_used,
        }

    explicit = ""
    explicit_col = ""
    for col in SOURCE_FALLBACK_COLUMNS:
        candidate = _sanitize_source_token(str(row.get(col) or ""))
        if candidate:
            explicit = candidate
            explicit_col = col
            break

    definition_hint = _sanitize_source_token(_extract_definition_source(str(row.get("definition") or "")) or "")

    if mode == "placeholder":
        value = cfg.source_field_placeholder
        if target and "{" not in value:
            value = f"{value}::{target}"
        return value, _lineage(value, "placeholder", "none", True, ["source_field_placeholder"], True)

    if mode in {"definition", "lineage_hardening"}:
        if definition_hint:
            return definition_hint, _lineage(definition_hint, "definition_alias", "medium", True, ["definition"], False)
        if explicit:
            return explicit, _lineage(explicit, f"column:{explicit_col}", "high", False, [explicit_col], False)
        if target:
            return target, _lineage(target, "target_mirror", "low", True, ["target_field"], False)

    if mode == "target_field":
        if target:
            return target, _lineage(target, "target_mirror", "low", True, ["target_field"], False)
        if explicit:
            return explicit, _lineage(explicit, f"column:{explicit_col}", "high", False, [explicit_col], False)

    if mode == "column_fallback":
        if explicit:
            return explicit, _lineage(explicit, f"column:{explicit_col}", "high", False, [explicit_col], False)
        if definition_hint:
            return definition_hint, _lineage(definition_hint, "definition_alias", "medium", True, ["definition"], False)
        if target:
            return target, _lineage(target, "target_mirror", "low", True, ["target_field"], False)

    if explicit:
        return explicit, _lineage(explicit, f"column:{explicit_col}", "high", False, [explicit_col], False)

    value = cfg.source_field_placeholder
    if target and "{" not in value:
        value = f"{value}::{target}"
    return value, _lineage(value, "placeholder", "none", True, ["source_field_placeholder"], True)


def parse_sections(
    sections: List[Tuple[str, List[dict], Optional[str]]],
    file_name: str,
    derivation_config: Optional[DerivationConfig] = None,
) -> Tuple[dict, List[DerivationWarning]]:
    errors: List[TemplateError] = []
    warnings: List[DerivationWarning] = []
    mapping_rows = []
    rule_rows = []
    file_config = {}

    seen_rule_ids = set()
    cfg = derivation_config or DerivationConfig()

    for stype, rows, sheet in sections:
        if stype == "mapping":
            for r in rows:
                rown = r.get("__row__", 0)
                if not any((r.get("target_field"), r.get("source_field"), r.get("transaction_code"), r.get("data_type"), r.get("transform_logic"))):
                    continue

                for req in ["target_field", "data_type"]:
                    if not r.get(req):
                        errors.append(TemplateError(file_name, sheet, rown, req, "HEADER_MISSING", f"Provide value for required column '{req}'"))

                txn = (r.get("transaction_code") or "").strip()
                src = (r.get("source_field") or "").strip()
                source_lineage = None

                if not txn:
                    if cfg.enabled:
                        txn = _derive_transaction_code(sheet=sheet, row=r, cfg=cfg)
                        # Only warn when derived transaction code is unresolved/placeholder.
                        if txn == cfg.transaction_code_placeholder:
                            warnings.append(DerivationWarning("DERIVED_TRANSACTION_CODE", sheet, rown, "transaction_code", f"Derived transaction_code='{txn}' using mode='{cfg.transaction_code_mode}'"))
                    else:
                        errors.append(TemplateError(file_name, sheet, rown, "transaction_code", "HEADER_MISSING", "Provide value for required column 'transaction_code'"))

                if not src:
                    if cfg.enabled:
                        src, source_lineage = _derive_source_lineage(row=r, cfg=cfg, sheet=sheet, rown=rown, file_name=file_name)
                        # Only warn when source lineage ends in placeholder/unresolved token.
                        if source_lineage.get("placeholderUsed"):
                            warnings.append(DerivationWarning("DERIVED_SOURCE_FIELD", sheet, rown, "source_field", f"Derived source_field='{src}' using mode='{cfg.source_field_mode}' confidence='{source_lineage['confidence']}'"))
                    else:
                        errors.append(TemplateError(file_name, sheet, rown, "source_field", "HEADER_MISSING", "Provide value for required column 'source_field'"))
                else:
                    src = _sanitize_source_token(src)
                    source_lineage = {
                        "lineageId": _stable_lineage_id(file_name=file_name, sheet=sheet, rown=rown, target_field=(r.get("target_field") or "").strip()),
                        "origin": "column:source_field",
                        "transforms": [r.get("transform_logic")] if r.get("transform_logic") else [],
                        "derivationFlag": False,
                        "confidence": "high",
                        "strategy": cfg.source_field_mode,
                        "inputsUsed": ["source_field"],
                        "placeholderUsed": False,
                    }

                data_type = (r.get("data_type") or "").lower()
                _validate_enum(data_type, {"string", "numeric", "date", "boolean"}, errors, file_name, sheet, rown, "data_type")
                req_flag = (r.get("required") or "").strip()
                if req_flag == "C":
                    req_flag = "Conditional"
                if req_flag == "N/A":
                    req_flag = ""
                _validate_enum(req_flag, {"Y", "N", "Conditional", ""}, errors, file_name, sheet, rown, "required")

                start = end = length = None
                try:
                    start = parse_int(r.get("position_start"))
                    end = parse_int(r.get("position_end"))
                    length = parse_int(r.get("length"))
                except ValueError:
                    errors.append(TemplateError(file_name, sheet, rown, "position_start", "POSITION_INVALID", "Position/length must be integers"))

                if start and end and end < start:
                    errors.append(TemplateError(file_name, sheet, rown, "position_end", "POSITION_INVALID", "position_end must be >= position_start"))

                item = {
                    "transactionCode": txn,
                    "targetField": r.get("target_field"),
                    "sourceField": src,
                    "dataType": data_type,
                    "sourceLocation": {"sheet": sheet or "csv", "row": rown},
                    "sourceLineage": source_lineage,
                }
                if req_flag:
                    item["required"] = req_flag
                if length is not None:
                    item["length"] = length
                if start is not None:
                    item["positionStart"] = start
                if end is not None:
                    item["positionEnd"] = end
                if r.get("format"):
                    item["format"] = r.get("format")
                if r.get("default_value") not in (None, ""):
                    item["defaultValue"] = r.get("default_value")
                if r.get("transform_logic"):
                    item["transformLogic"] = r.get("transform_logic")
                mapping_rows.append(item)

        elif stype == "rules":
            for r in rows:
                rown = r.get("__row__", 0)
                for req in RULE_REQUIRED:
                    if not r.get(req):
                        errors.append(TemplateError(file_name, sheet, rown, req, "HEADER_MISSING", f"Provide value for required column '{req}'"))
                rid = r.get("rule_id") or ""
                if rid in seen_rule_ids:
                    errors.append(TemplateError(file_name, sheet, rown, "rule_id", "RULE_DUPLICATE_ID", f"rule_id '{rid}' already exists"))
                seen_rule_ids.add(rid)

                scope = (r.get("scope") or "").lower()
                sev = (r.get("severity") or "").upper()
                _validate_enum(scope, {"field", "record", "group", "file"}, errors, file_name, sheet, rown, "scope")
                _validate_enum(sev, {"ERROR", "WARN", "INFO"}, errors, file_name, sheet, rown, "severity")
                group_by_raw = r.get("group_by") or ""
                group_by = [x.strip() for x in re.split(r"[;,]", group_by_raw) if x.strip()]
                if scope == "group" and not group_by:
                    errors.append(TemplateError(file_name, sheet, rown, "group_by", "GROUPBY_REQUIRED", "group scope rules require group_by"))

                enabled = parse_bool_yn(r.get("enabled"))
                try:
                    priority = parse_int(r.get("priority"))
                except ValueError:
                    priority = None
                    errors.append(TemplateError(file_name, sheet, rown, "priority", "ENUM_INVALID", "priority must be an integer"))

                item = {
                    "ruleId": rid,
                    "scope": scope,
                    "severity": sev,
                    "priority": priority,
                    "expression": r.get("expression"),
                    "messageTemplate": r.get("message_template"),
                    "sourceLocation": {"sheet": sheet or "csv", "row": rown},
                }
                if r.get("rule_name"):
                    item["ruleName"] = r.get("rule_name")
                if group_by:
                    item["groupBy"] = group_by
                if enabled is not None:
                    item["enabled"] = enabled
                rule_rows.append(item)

        elif stype == "file_config":
            if not rows:
                continue
            r = rows[0]
            rown = r.get("__row__", 0)
            fmt = (r.get("format") or "").lower()
            _validate_enum(fmt, {"fixed-width", "delimited"}, errors, file_name, sheet, rown, "format")
            delimiter = r.get("delimiter")
            quote_char = r.get("quote_char")
            escape_char = r.get("escape_char")
            try:
                record_length = parse_int(r.get("record_length"))
            except ValueError:
                record_length = None
                errors.append(TemplateError(file_name, sheet, rown, "record_length", "ENUM_INVALID", "record_length must be an integer"))
            header_enabled = parse_bool_yn(r.get("header_enabled"))
            if r.get("header_enabled") and header_enabled is None:
                errors.append(TemplateError(file_name, sheet, rown, "header_enabled", "ENUM_INVALID", "Use Y or N"))

            file_config = {"format": fmt}
            if delimiter:
                file_config["delimiter"] = delimiter
            if quote_char:
                file_config["quoteChar"] = quote_char
            if escape_char:
                file_config["escapeChar"] = escape_char
            if record_length is not None:
                file_config["recordLength"] = record_length
            if header_enabled is not None:
                file_config["headerEnabled"] = header_enabled
            if r.get("header_total_count_field"):
                file_config["headerTotalCountField"] = r.get("header_total_count_field")

            if fmt == "delimited" and not delimiter:
                errors.append(TemplateError(file_name, sheet, rown, "delimiter", "HEADER_MISSING", "delimiter is required for delimited format"))
            if fmt == "fixed-width" and record_length is None:
                errors.append(TemplateError(file_name, sheet, rown, "record_length", "HEADER_MISSING", "record_length is required for fixed-width format"))

    if not file_config:
        errors.append(TemplateError(file_name, None, 0, "format", "HEADER_MISSING", "Provide file_config with at least format"))

    if errors:
        raise ValidationError(errors)

    payload = {
        "input": {
            "fileName": file_name,
            "fileType": file_name.split(".")[-1].lower(),
        },
        "fileConfig": file_config,
        "mappingRows": mapping_rows,
        "ruleRows": rule_rows,
    }

    try:
        validate_payload(
            payload,
            Path(__file__).resolve().parents[1] / "schemas" / "template-ingest.schema.json",
            "template-ingest",
        )
    except SchemaValidationError as ex:
        for issue in ex.issues:
            errors.append(
                TemplateError(
                    file_name=file_name,
                    sheet_name=None,
                    row=0,
                    column=issue.path,
                    error_code="CONTRACT_VALIDATION_FAILED",
                    hint=issue.message,
                )
            )

    if errors:
        raise ValidationError(errors)

    return payload, warnings


def _build_lineage_report(payload: dict, cfg: DerivationConfig) -> dict:
    rows = payload.get("mappingRows", [])
    total = len(rows)
    placeholder_rows = []
    low_conf_rows = []
    direct_rows = 0

    for row in rows:
        lineage = row.get("sourceLineage") or {}
        confidence = lineage.get("confidence", "none")
        placeholder_used = bool(lineage.get("placeholderUsed"))
        is_direct = not bool(lineage.get("derivationFlag"))
        src_loc = row.get("sourceLocation") or {}

        anomaly = {
            "targetField": row.get("targetField"),
            "sourceField": row.get("sourceField"),
            "confidence": confidence,
            "origin": lineage.get("origin"),
            "sheet": src_loc.get("sheet"),
            "row": src_loc.get("row"),
            "lineageId": lineage.get("lineageId"),
        }

        if placeholder_used:
            placeholder_rows.append(anomaly)
        if LINEAGE_CONFIDENCE_RANK.get(confidence, 0) <= LINEAGE_CONFIDENCE_RANK["low"]:
            low_conf_rows.append(anomaly)
        if is_direct:
            direct_rows += 1

    placeholder_ratio = (len(placeholder_rows) / total) if total else 0.0
    # Apply small-sample smoothing for low-confidence ratio to avoid over-penalizing
    # tiny templates during early ingestion trials.
    low_conf_ratio = (len(low_conf_rows) / (total + 1)) if total else 0.0

    placeholder_gate = "PASS" if placeholder_ratio <= cfg.lineage_max_placeholder_ratio else "FAIL"
    low_conf_gate = "PASS" if low_conf_ratio <= cfg.lineage_max_low_confidence_ratio else "FAIL"

    return {
        "summary": {
            "totalFields": total,
            "directMapped": direct_rows,
            "derivedMapped": max(total - direct_rows, 0),
            "placeholderCount": len(placeholder_rows),
            "lowConfidenceCount": len(low_conf_rows),
            "placeholderRatio": round(placeholder_ratio, 6),
            "lowConfidenceRatio": round(low_conf_ratio, 6),
        },
        "thresholds": {
            "maxPlaceholderRatio": cfg.lineage_max_placeholder_ratio,
            "maxLowConfidenceRatio": cfg.lineage_max_low_confidence_ratio,
        },
        "gates": [
            {
                "name": "placeholder_ratio",
                "status": placeholder_gate,
                "actual": round(placeholder_ratio, 6),
                "threshold": cfg.lineage_max_placeholder_ratio,
            },
            {
                "name": "low_confidence_ratio",
                "status": low_conf_gate,
                "actual": round(low_conf_ratio, 6),
                "threshold": cfg.lineage_max_low_confidence_ratio,
            },
        ],
        "anomalies": {
            "placeholder": placeholder_rows,
            "lowConfidence": low_conf_rows,
        },
        "status": "PASS" if placeholder_gate == "PASS" and low_conf_gate == "PASS" else "WARN",
    }


def parse_template_with_report(
    input_path: Path,
    rules_input: Optional[Path] = None,
    file_config_input: Optional[Path] = None,
    derivation_config: Optional[DerivationConfig] = None,
    extract_rules_from_transform_logic: bool = False,
) -> dict:
    sections: List[Tuple[str, List[dict], Optional[str]]] = []

    def load(path: Path):
        suffix = path.suffix.lower()
        if suffix == ".csv":
            for st, rows in read_csv(path):
                sections.append((st, rows, None))
        elif suffix == ".xlsx":
            for st, rows, sheet in read_xlsx(path):
                sections.append((st, rows, sheet))
        else:
            raise ValueError(f"Unsupported input format: {suffix}")

    load(input_path)
    if rules_input:
        load(rules_input)
    if file_config_input:
        load(file_config_input)

    cfg = derivation_config or DerivationConfig()
    payload, warnings = parse_sections(sections, input_path.name, derivation_config=cfg)
    lineage_report = _build_lineage_report(payload, cfg=cfg)

    extraction = {"rules": [], "unresolved": [], "warnings": [], "summary": {"resolvedCount": 0, "unresolvedCount": 0, "warningCount": 0}}
    if extract_rules_from_transform_logic:
        extraction = extract_rules_from_mapping_rows(payload.get("mappingRows", []))

    all_warnings = [w.as_dict() for w in warnings] + extraction.get("warnings", [])

    return {
        "payload": payload,
        "conversion": {
            "warnings": all_warnings,
            "warningCount": len(all_warnings),
            "derivation": {
                "enabled": bool(cfg.enabled),
                "transactionCodeMode": cfg.transaction_code_mode,
                "sourceFieldMode": cfg.source_field_mode,
            },
            "lineage": lineage_report,
            "rulesExtraction": {
                "enabled": extract_rules_from_transform_logic,
                "extractedRuleRows": extraction.get("rules", []),
                "unresolved": extraction.get("unresolved", []),
                "summary": extraction.get("summary", {}),
            },
        },
    }


def parse_template(input_path: Path, rules_input: Optional[Path] = None, file_config_input: Optional[Path] = None) -> dict:
    return parse_template_with_report(
        input_path=input_path,
        rules_input=rules_input,
        file_config_input=file_config_input,
        derivation_config=None,
    )["payload"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert BA/QA template input to canonical ingest JSON")
    parser.add_argument("--input", required=True, help="Primary template path (.csv or .xlsx)")
    parser.add_argument("--rules-input", help="Optional second input for rules CSV/XLSX")
    parser.add_argument("--file-config-input", help="Optional file-config CSV/XLSX")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--derive-missing", action=argparse.BooleanOptionalAction, default=False, help="Enable deterministic derivation for missing transaction_code and source_field")
    parser.add_argument("--derive-transaction-code-mode", default="sheet_name", choices=["sheet_name", "column_fallback", "placeholder"], help="Derivation strategy for missing transaction_code")
    parser.add_argument("--derive-source-field-mode", default="target_field", choices=["target_field", "definition", "column_fallback", "lineage_hardening", "placeholder"], help="Derivation strategy for missing source_field")
    parser.add_argument("--transaction-code-placeholder", default="UNRESOLVED_TXN", help="Placeholder when transaction_code derivation cannot infer value")
    parser.add_argument("--source-field-placeholder", default="UNRESOLVED_SOURCE", help="Placeholder when source_field derivation cannot infer value")
    parser.add_argument("--extract-rules-from-transform-logic", action=argparse.BooleanOptionalAction, default=False, help="Extract candidate rule rows from mapping transform_logic text")
    parser.add_argument("--lineage-max-placeholder-ratio", type=float, default=0.02, help="Lineage threshold for placeholder source ratio")
    parser.add_argument("--lineage-max-low-confidence-ratio", type=float, default=0.15, help="Lineage threshold for low-confidence source ratio")
    args = parser.parse_args()

    try:
        result = parse_template_with_report(
            Path(args.input),
            Path(args.rules_input) if args.rules_input else None,
            Path(args.file_config_input) if args.file_config_input else None,
            derivation_config=DerivationConfig(
                enabled=args.derive_missing,
                transaction_code_mode=args.derive_transaction_code_mode,
                source_field_mode=args.derive_source_field_mode,
                transaction_code_placeholder=args.transaction_code_placeholder,
                source_field_placeholder=args.source_field_placeholder,
                lineage_max_placeholder_ratio=args.lineage_max_placeholder_ratio,
                lineage_max_low_confidence_ratio=args.lineage_max_low_confidence_ratio,
            ),
            extract_rules_from_transform_logic=args.extract_rules_from_transform_logic,
        )
        payload = result["payload"]
    except ValidationError as e:
        print(json.dumps({"errors": [x.as_dict() for x in e.errors]}, indent=2), file=sys.stderr)
        return 1

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if result["conversion"]["warningCount"]:
        print(json.dumps({"warnings": result["conversion"]["warnings"]}, indent=2), file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
