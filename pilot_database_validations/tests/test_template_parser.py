import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.template_parser import DerivationConfig, ValidationError, parse_template, parse_template_with_report


def _make_xlsx(path: Path, sheets: dict[str, list[list[str]]]):
    content_types = """<?xml version='1.0' encoding='UTF-8'?>
<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>
  <Default Extension='rels' ContentType='application/vnd.openxmlformats-package.relationships+xml'/>
  <Default Extension='xml' ContentType='application/xml'/>
  <Override PartName='/xl/workbook.xml' ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'/>
  <Override PartName='/xl/sharedStrings.xml' ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml'/>
  <Override PartName='/xl/worksheets/sheet1.xml' ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'/>
  <Override PartName='/xl/worksheets/sheet2.xml' ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'/>
  <Override PartName='/xl/worksheets/sheet3.xml' ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'/>
</Types>"""
    rels = """<?xml version='1.0' encoding='UTF-8'?>
<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>
  <Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument' Target='xl/workbook.xml'/>
</Relationships>"""

    all_strings = []
    idx = {}

    def s_index(v):
        if v not in idx:
            idx[v] = len(all_strings)
            all_strings.append(v)
        return idx[v]

    def col(n):
        out = ""
        n += 1
        while n:
            n, r = divmod(n - 1, 26)
            out = chr(65 + r) + out
        return out

    def sheet_xml(rows):
        xml_rows = []
        for r_i, row in enumerate(rows, start=1):
            cells = []
            for c_i, val in enumerate(row):
                sidx = s_index(str(val))
                cells.append(f"<c r='{col(c_i)}{r_i}' t='s'><v>{sidx}</v></c>")
            xml_rows.append(f"<row r='{r_i}'>{''.join(cells)}</row>")
        return """<?xml version='1.0' encoding='UTF-8'?>
<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>
  <sheetData>{rows}</sheetData>
</worksheet>""".format(rows="".join(xml_rows))

    sheet_names = list(sheets.keys())
    workbook = """<?xml version='1.0' encoding='UTF-8'?>
<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
 xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>
  <sheets>
    <sheet name='{s1}' sheetId='1' r:id='rId1'/>
    <sheet name='{s2}' sheetId='2' r:id='rId2'/>
    <sheet name='{s3}' sheetId='3' r:id='rId3'/>
  </sheets>
</workbook>""".format(s1=sheet_names[0], s2=sheet_names[1], s3=sheet_names[2])
    wb_rels = """<?xml version='1.0' encoding='UTF-8'?>
<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>
  <Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet' Target='worksheets/sheet1.xml'/>
  <Relationship Id='rId2' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet' Target='worksheets/sheet2.xml'/>
  <Relationship Id='rId3' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet' Target='worksheets/sheet3.xml'/>
</Relationships>"""

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        for i, name in enumerate(sheet_names, start=1):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", sheet_xml(sheets[name]))
        sst_items = "".join(f"<si><t>{s}</t></si>" for s in all_strings)
        sst = f"""<?xml version='1.0' encoding='UTF-8'?>
<sst xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main' count='{len(all_strings)}' uniqueCount='{len(all_strings)}'>
{sst_items}
</sst>"""
        zf.writestr("xl/sharedStrings.xml", sst)


class TemplateParserTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def test_parse_csv_inputs(self):
        payload = parse_template(
            self.root / "examples/templates/mapping-template.csv",
            self.root / "examples/templates/rules-template.csv",
            self.root / "examples/templates/file-config-template.csv",
        )
        self.assertEqual(payload["input"]["fileType"], "csv")
        self.assertEqual(payload["fileConfig"]["format"], "delimited")
        self.assertEqual(len(payload["mappingRows"]), 4)
        self.assertEqual(len(payload["ruleRows"]), 4)
        self.assertTrue(all("sourceLocation" in r for r in payload["mappingRows"]))
        self.assertIsInstance(payload["ruleRows"][0]["priority"], int)

    def test_duplicate_rule_id_raises(self):
        with tempfile.TemporaryDirectory() as td:
            dup_rules = Path(td) / "rules.csv"
            dup_rules.write_text(
                "rule_id,scope,severity,priority,expression,message_template\n"
                "R1,record,ERROR,1,a,b\n"
                "R1,record,ERROR,2,c,d\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValidationError) as ex:
                parse_template(
                    self.root / "examples/templates/mapping-template.csv",
                    dup_rules,
                    self.root / "examples/templates/file-config-template.csv",
                )
            self.assertTrue(any(e.error_code == "RULE_DUPLICATE_ID" for e in ex.exception.errors))

    def test_parse_xlsx_multi_sheet(self):
        with tempfile.TemporaryDirectory() as td:
            xlsx = Path(td) / "template.xlsx"
            _make_xlsx(
                xlsx,
                {
                    "Mapping": [
                        ["transaction_code", "target_field", "source_field", "data_type"],
                        ["1", "F1", "S1", "string"],
                    ],
                    "Rules": [
                        ["rule_id", "scope", "severity", "priority", "expression", "message_template"],
                        ["R1", "record", "ERROR", "1", "x == y", "bad"],
                    ],
                    "FileConfig": [
                        ["format", "delimiter", "header_enabled"],
                        ["delimited", "|", "Y"],
                    ],
                },
            )
            payload = parse_template(xlsx)
            self.assertEqual(payload["input"]["fileType"], "xlsx")
            self.assertEqual(payload["fileConfig"]["delimiter"], "|")
            self.assertEqual(payload["ruleRows"][0]["ruleId"], "R1")

    def test_derives_missing_mapping_fields_with_config(self):
        with tempfile.TemporaryDirectory() as td:
            xlsx = Path(td) / "template.xlsx"
            _make_xlsx(
                xlsx,
                {
                    "TXN_010": [
                        ["target_field", "definition", "data_type", "position_start", "length"],
                        ["AMOUNT", "SRC_AMT", "numeric", "1.0", "5.0"],
                    ],
                    "Rules": [
                        ["rule_id", "scope", "severity", "priority", "expression", "message_template"],
                        ["R1", "record", "ERROR", "1.0", "x == y", "bad"],
                    ],
                    "FileConfig": [["format", "delimiter"], ["delimited", "|"]],
                },
            )
            result = parse_template_with_report(
                xlsx,
                derivation_config=DerivationConfig(enabled=True, transaction_code_mode="sheet_name", source_field_mode="definition"),
            )
            payload = result["payload"]
            self.assertEqual(payload["mappingRows"][0]["transactionCode"], "TXN_010")
            self.assertEqual(payload["mappingRows"][0]["sourceField"], "SRC_AMT")
            self.assertEqual(payload["mappingRows"][0]["sourceLineage"]["origin"], "definition_alias")
            self.assertEqual(payload["mappingRows"][0]["sourceLineage"]["confidence"], "medium")
            self.assertEqual(payload["mappingRows"][0]["positionStart"], 1)
            self.assertEqual(payload["mappingRows"][0]["length"], 5)
            self.assertEqual(payload["ruleRows"][0]["priority"], 1)
            self.assertEqual(result["conversion"]["warningCount"], 2)

    def test_missing_mapping_fields_fail_without_derivation(self):
        with tempfile.TemporaryDirectory() as td:
            mapping = Path(td) / "mapping.csv"
            mapping.write_text("target_field,data_type\nACCOUNT_ID,string\n", encoding="utf-8")
            file_cfg = Path(td) / "file-config.csv"
            file_cfg.write_text("format,delimiter\ndelimited,|\n", encoding="utf-8")
            with self.assertRaises(ValidationError) as ex:
                parse_template(mapping, file_config_input=file_cfg)
            self.assertTrue(any(e.column in {"transaction_code", "source_field"} for e in ex.exception.errors))

    def test_extracts_rules_from_transform_logic_when_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            mapping = Path(td) / "mapping.csv"
            mapping.write_text(
                "transaction_code,target_field,source_field,data_type,transform_logic\n"
                "32010,TRN-COD-ERT,TRN_COD,numeric,Default to '32010'\n"
                "32010,REF-NUM-ERT,REF_NUM,string,Leave Blank\n"
                "32010,OGL-TRM-ORI,ORG_TERM,numeric,IF (ORG-TERM > 999) THEN 999; ELSE ORG-TERM\n",
                encoding="utf-8",
            )
            file_cfg = Path(td) / "file-config.csv"
            file_cfg.write_text("format,delimiter\ndelimited,|\n", encoding="utf-8")
            result = parse_template_with_report(
                input_path=mapping,
                file_config_input=file_cfg,
                extract_rules_from_transform_logic=True,
            )
            extraction = result["conversion"]["rulesExtraction"]
            self.assertTrue(extraction["enabled"])
            self.assertEqual(extraction["summary"]["resolvedCount"], 2)
            self.assertEqual(extraction["summary"]["unresolvedCount"], 1)
            self.assertEqual(result["conversion"]["warningCount"], 1)


if __name__ == "__main__":
    unittest.main()
