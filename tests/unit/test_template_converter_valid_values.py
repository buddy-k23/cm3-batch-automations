from src.config.template_converter import TemplateConverter


def test_template_converter_parses_pipe_separated_valid_values(tmp_path):
    csv_file = tmp_path / "mapping.csv"
    csv_file.write_text(
        "Field Name,Data Type,Valid Values\n"
        "status,String,ACTIVE|INACTIVE|CLOSED\n",
        encoding="utf-8",
    )

    converter = TemplateConverter()
    mapping = converter.from_csv(str(csv_file), mapping_name="test_mapping", file_format="pipe_delimited")

    field = mapping["fields"][0]
    assert field["valid_values"] == ["ACTIVE", "INACTIVE", "CLOSED"]
    assert {"type": "in_list", "parameters": {"values": ["ACTIVE", "INACTIVE", "CLOSED"]}} in field["validation_rules"]


def test_template_converter_parses_comma_separated_valid_values(tmp_path):
    csv_file = tmp_path / "mapping.csv"
    csv_file.write_text(
        "Field Name,Data Type,Valid Values\n"
        "status,String,ACTIVE,INACTIVE,CLOSED\n",
        encoding="utf-8",
    )

    # Quote the value so CSV parser keeps it in one column
    csv_file.write_text(
        'Field Name,Data Type,Valid Values\nstatus,String,"ACTIVE,INACTIVE,CLOSED"\n',
        encoding="utf-8",
    )

    converter = TemplateConverter()
    mapping = converter.from_csv(str(csv_file), mapping_name="test_mapping", file_format="pipe_delimited")

    field = mapping["fields"][0]
    assert field["valid_values"] == ["ACTIVE", "INACTIVE", "CLOSED"]


# ---------------------------------------------------------------------------
# Regression tests for ADR 0007 — preserve string literals on CSV/Excel read
# ---------------------------------------------------------------------------
#
# Before the fix, ``pd.read_csv(csv_path)`` (no ``dtype=str``) caused pandas
# to auto-infer numeric columns. A ``Valid Values`` column that contained
# only integer literals plus blanks was inferred as ``float64``, so a cell
# like ``100030`` became the float ``100030.0`` and ``str(...).strip()`` in
# ``_convert_row_to_field`` emitted ``'100030.0'`` into the JSON's
# ``valid_values``. The downstream fixed-width validator then reported
# false positives for every row, comparing the source data's ``'100030'``
# against ``'100030.0'``. Surfaced by the SHAW ATOCTRAN smoke after ADR
# 0006 MR 2 unblocked real validation; see
# ``docs/handover/SHAW_atoctran_smoke_findings.md``.


def test_template_converter_preserves_numeric_only_valid_values(tmp_path):
    """Numeric-only ``Valid Values`` cells keep their original string form.

    Without ``dtype=str`` the column would be auto-inferred as ``float64``
    and ``str(100030.0)`` would emit ``'100030.0'``.
    """
    csv_file = tmp_path / "mapping.csv"
    # Three rows: numeric Valid Values, blank, numeric — exactly the shape
    # that triggers ``float64`` inference (blanks force pandas to use a
    # nullable type; with no string cells it picks float).
    csv_file.write_text(
        "Field Name,Data Type,Position,Length,Valid Values\n"
        "location_code,String,1,6,100030\n"
        "acct_num,String,7,18,\n"
        "txn_code,String,25,3,900\n",
        encoding="utf-8",
    )

    converter = TemplateConverter()
    mapping = converter.from_csv(
        str(csv_file), mapping_name="numeric_vv", file_format="fixed_width"
    )

    location_field = mapping["fields"][0]
    assert location_field["valid_values"] == ["100030"], (
        f"Expected ['100030'] but got {location_field['valid_values']!r}; "
        "this is the ADR 0007 regression."
    )
    # And the rule body must match exactly.
    assert {
        "type": "in_list",
        "parameters": {"values": ["100030"]},
    } in location_field["validation_rules"]

    txn_field = mapping["fields"][2]
    assert txn_field["valid_values"] == ["900"]


def test_template_converter_preserves_pipe_separated_numeric_valid_values(tmp_path):
    """Pipe-separated numeric Valid Values keep their original string form.

    This is the SHAW ATOCTRAN ``LOCATION-CODE`` shape from sheet 100:
    ``100030|100040|200000``.
    """
    csv_file = tmp_path / "mapping.csv"
    csv_file.write_text(
        "Field Name,Data Type,Position,Length,Valid Values\n"
        "location_code,String,1,6,100030|100040|200000\n",
        encoding="utf-8",
    )

    converter = TemplateConverter()
    mapping = converter.from_csv(
        str(csv_file), mapping_name="pipe_numeric", file_format="fixed_width"
    )

    field = mapping["fields"][0]
    assert field["valid_values"] == ["100030", "100040", "200000"]
    assert ".0" not in str(field["valid_values"]), (
        "ADR 0007 regression: numeric literals must not carry a '.0' suffix."
    )


def test_template_converter_position_length_still_coerce_to_int(tmp_path):
    """``dtype=str`` does not break ``Position``/``Length`` int coercion.

    ``_convert_row_to_field`` does ``int(row['Position'])`` /
    ``int(row['Length'])`` — both work on the string ``'1'`` just as they
    worked on the int64 ``1``. Sanity check that the ADR 0007 fix does
    not regress fixed-width parsing.
    """
    csv_file = tmp_path / "mapping.csv"
    csv_file.write_text(
        "Field Name,Data Type,Position,Length,Required\n"
        "f1,String,1,6,Y\n"
        "f2,Integer,7,12,N\n",
        encoding="utf-8",
    )

    converter = TemplateConverter()
    mapping = converter.from_csv(
        str(csv_file), mapping_name="fw_basic", file_format="fixed_width"
    )

    assert mapping["fields"][0]["position"] == 1
    assert mapping["fields"][0]["length"] == 6
    assert mapping["fields"][0]["required"] is True
    assert mapping["fields"][1]["position"] == 7
    assert mapping["fields"][1]["length"] == 12
    assert mapping["fields"][1]["required"] is False
    assert mapping["total_record_length"] == 18
