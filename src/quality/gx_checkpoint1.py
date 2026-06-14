"""Great Expectations Checkpoint 1 runner (BA-friendly, config-first)."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from html import escape

import pandas as pd


@dataclass
class TargetConfig:
    target_id: str
    data_file: str
    delimiter: str = "|"
    has_header: bool = False
    mapping_file: str | None = None
    key_columns: list[str] | None = None
    required_columns: list[str] | None = None


@dataclass
class ExpectationConfig:
    target_id: str
    expectation_type: str
    enabled: bool = True
    column: str | None = None
    column_list: list[str] | None = None
    value_set: list[str] | None = None
    min_value: float | int | None = None
    max_value: float | int | None = None
    mostly: float | None = None
    notes: str | None = None


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def _as_list(value: str | None) -> list[str] | None:
    if value is None or str(value).strip() == "":
        return None
    return [x.strip() for x in str(value).split("|") if x.strip()]


def _as_number(value: str | None) -> float | int | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    return float(text)


def _load_mapping(mapping_file: str) -> dict[str, Any]:
    with open(mapping_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_mapping_columns(mapping_file: str) -> list[str]:
    mapping = _load_mapping(mapping_file)

    if "fields" in mapping:
        return [f["name"] for f in mapping["fields"]]
    if "mappings" in mapping:
        return [m["source_column"] for m in mapping["mappings"]]
    raise ValueError(f"Unsupported mapping format in {mapping_file}")


def load_targets_csv(path: str) -> list[TargetConfig]:
    targets: list[TargetConfig] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"target_id", "data_file"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"Targets file must include columns: {sorted(required)}")

        for row in reader:
            if _as_bool(row.get("enabled"), default=True) is False:
                continue
            targets.append(
                TargetConfig(
                    target_id=row["target_id"].strip(),
                    data_file=row["data_file"].strip(),
                    delimiter=(row.get("delimiter") or "|").strip() or "|",
                    has_header=_as_bool(row.get("has_header"), default=False),
                    mapping_file=(row.get("mapping_file") or "").strip() or None,
                    key_columns=_as_list(row.get("key_columns")),
                    required_columns=_as_list(row.get("required_columns")),
                )
            )
    return targets


def load_expectations_csv(path: str) -> list[ExpectationConfig]:
    expectations: list[ExpectationConfig] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"target_id", "expectation_type"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(f"Expectations file must include columns: {sorted(required)}")

        for row in reader:
            if _as_bool(row.get("enabled"), default=True) is False:
                continue
            expectations.append(
                ExpectationConfig(
                    target_id=row["target_id"].strip(),
                    expectation_type=row["expectation_type"].strip(),
                    enabled=True,
                    column=(row.get("column") or "").strip() or None,
                    column_list=_as_list(row.get("column_list")),
                    value_set=_as_list(row.get("value_set")),
                    min_value=_as_number(row.get("min_value")),
                    max_value=_as_number(row.get("max_value")),
                    mostly=_as_number(row.get("mostly")),
                    notes=(row.get("notes") or "").strip() or None,
                )
            )
    return expectations


def _read_target_data(target: TargetConfig) -> pd.DataFrame:
    header = 0 if target.has_header else None

    mapping = _load_mapping(target.mapping_file) if target.mapping_file else None
    source_format = ((mapping or {}).get("source") or {}).get("format")

    if mapping and source_format == "fixed_width" and "fields" in mapping:
        widths = [int(f["length"]) for f in mapping["fields"]]
        names = [f["name"] for f in mapping["fields"]]
        df = pd.read_fwf(target.data_file, widths=widths, names=names, dtype=str, header=None)
        return df

    df = pd.read_csv(target.data_file, sep=target.delimiter, dtype=str, header=header)

    if not target.has_header:
        if target.mapping_file:
            df.columns = _load_mapping_columns(target.mapping_file)
        else:
            df.columns = [f"col_{i+1}" for i in range(df.shape[1])]
    return df


def _add_default_expectations(validator: Any, target: TargetConfig, df: pd.DataFrame) -> None:
    if target.mapping_file:
        expected_columns = _load_mapping_columns(target.mapping_file)
        validator.expect_table_columns_to_match_ordered_list(column_list=expected_columns)

    if target.required_columns:
        for col in target.required_columns:
            validator.expect_column_values_to_not_be_null(column=col)

    if target.key_columns:
        for col in target.key_columns:
            validator.expect_column_values_to_not_be_null(column=col)
            validator.expect_column_values_to_be_unique(column=col)

    validator.expect_table_row_count_to_be_between(min_value=1, max_value=max(len(df), 1_000_000_000))


def _apply_expectation(validator: Any, exp: ExpectationConfig) -> None:
    et = exp.expectation_type

    if et == "expect_table_columns_to_match_ordered_list":
        validator.expect_table_columns_to_match_ordered_list(column_list=exp.column_list or [])
    elif et == "expect_column_values_to_not_be_null":
        validator.expect_column_values_to_not_be_null(column=exp.column)
    elif et == "expect_column_values_to_be_unique":
        validator.expect_column_values_to_be_unique(column=exp.column)
    elif et == "expect_column_values_to_be_in_set":
        validator.expect_column_values_to_be_in_set(column=exp.column, value_set=exp.value_set or [])
    elif et == "expect_column_values_to_be_between":
        kwargs: dict[str, Any] = {"column": exp.column, "min_value": exp.min_value, "max_value": exp.max_value}
        if exp.mostly is not None:
            kwargs["mostly"] = exp.mostly
        validator.expect_column_values_to_be_between(**kwargs)
    elif et == "expect_table_row_count_to_be_between":
        validator.expect_table_row_count_to_be_between(min_value=exp.min_value, max_value=exp.max_value)
    else:
        raise ValueError(f"Unsupported expectation_type: {et}")


def _flatten_results_for_csv(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in summary.get("targets", []):
        target_id = target.get("target_id")
        result = target.get("result", {})
        for r in result.get("results", []):
            cfg = r.get("expectation_config", {})
            kwargs = cfg.get("kwargs", {})
            rows.append(
                {
                    "target_id": target_id,
                    "run_success": target.get("success"),
                    "expectation_type": cfg.get("type"),
                    "column": kwargs.get("column"),
                    "success": r.get("success"),
                    "unexpected_count": (r.get("result") or {}).get("unexpected_count"),
                    "element_count": (r.get("result") or {}).get("element_count"),
                }
            )
    return rows


def _write_csv_summary(summary: dict[str, Any], csv_output: str) -> None:
    rows = _flatten_results_for_csv(summary)
    out_path = Path(csv_output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "target_id",
        "run_success",
        "expectation_type",
        "column",
        "success",
        "unexpected_count",
        "element_count",
    ]

    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_html_summary(summary: dict[str, Any], html_output: str) -> None:
    rows = _flatten_results_for_csv(summary)
    out_path = Path(html_output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    body_rows = "\n".join(
        "<tr>"
        f"<td>{escape(str(r.get('target_id', '')))}</td>"
        f"<td>{escape(str(r.get('expectation_type', '')))}</td>"
        f"<td>{escape(str(r.get('column', '')))}</td>"
        f"<td>{'✅' if r.get('success') else '❌'}</td>"
        f"<td>{escape(str(r.get('unexpected_count', '')))}</td>"
        f"<td>{escape(str(r.get('element_count', '')))}</td>"
        "</tr>"
        for r in rows
    )

    html = f"""
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <title>GX Checkpoint 1 Summary</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; }}
    th {{ background: #f5f5f5; }}
  </style>
</head>
<body>
  <h1>Great Expectations - Checkpoint 1</h1>
  <p><strong>Overall:</strong> {'PASS' if summary.get('success') else 'FAIL'}</p>
  <p><strong>Targets Run:</strong> {summary.get('targets_run', 0)}</p>
  <table>
    <thead>
      <tr>
        <th>Target</th><th>Expectation</th><th>Column</th><th>Pass</th><th>Unexpected</th><th>Element Count</th>
      </tr>
    </thead>
    <tbody>
      {body_rows}
    </tbody>
  </table>
</body>
</html>
""".strip()

    out_path.write_text(html, encoding="utf-8")


def run_checkpoint_1(
    targets_csv: str,
    expectations_csv: str,
    output_json: str | None = None,
    csv_output: str | None = None,
    html_output: str | None = None,
    data_docs_dir: str | None = None,
) -> dict[str, Any]:
    try:
        import great_expectations as gx
    except ImportError as exc:
        raise RuntimeError(
            "Great Expectations is not installed. Install with: pip install great_expectations"
        ) from exc

    targets = load_targets_csv(targets_csv)
    expectations = load_expectations_csv(expectations_csv)

    exp_by_target: dict[str, list[ExpectationConfig]] = {}
    for exp in expectations:
        exp_by_target.setdefault(exp.target_id, []).append(exp)

    context = gx.get_context(mode="ephemeral")

    run_results: list[dict[str, Any]] = []
    overall_success = True

    for target in targets:
        df = _read_target_data(target)

        ds_name = "app_checkpoint1_ds"
        suite_name = f"checkpoint1_{target.target_id}"
        asset_name = f"{target.target_id}_asset"
        batch_def_name = f"{target.target_id}_whole_df"

        datasource = context.data_sources.add_or_update_pandas(name=ds_name)
        asset = datasource.add_dataframe_asset(name=asset_name)
        batch_definition = asset.add_batch_definition_whole_dataframe(batch_def_name)

        try:
            batch_request = batch_definition.build_batch_request(batch_parameters={"dataframe": df})
        except TypeError:
            batch_request = batch_definition.build_batch_request(options={"dataframe": df})
        context.suites.add_or_update(gx.ExpectationSuite(name=suite_name))
        validator = context.get_validator(batch_request=batch_request, expectation_suite_name=suite_name)

        _add_default_expectations(validator, target, df)
        for exp in exp_by_target.get(target.target_id, []):
            _apply_expectation(validator, exp)

        result = validator.validate()
        success = bool(getattr(result, "success", False))
        overall_success = overall_success and success
        run_results.append(
            {
                "target_id": target.target_id,
                "success": success,
                "result": result.to_json_dict() if hasattr(result, "to_json_dict") else dict(result),
            }
        )

    summary = {
        "success": overall_success,
        "targets_run": len(targets),
        "targets": run_results,
    }

    if output_json:
        out_path = Path(output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if csv_output:
        _write_csv_summary(summary, csv_output)

    if html_output:
        _write_html_summary(summary, html_output)

    if data_docs_dir:
        # For ephemeral context, build_data_docs uses GE defaults.
        # We still call it to support users who rely on GE native docs.
        context.build_data_docs()

    return summary
