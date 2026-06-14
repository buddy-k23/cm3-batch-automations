import tempfile
import unittest
from pathlib import Path

from tools.template_parser import DerivationConfig, parse_template_with_report


class LineageHardeningTests(unittest.TestCase):
    def test_lineage_hardening_prefers_definition_alias_then_target(self):
        with tempfile.TemporaryDirectory() as td:
            mapping = Path(td) / "mapping.csv"
            mapping.write_text(
                "target_field,definition,data_type\n"
                "ACCOUNT_ID,source: SRC_ACCOUNT_ID,string\n"
                "BALANCE,,numeric\n",
                encoding="utf-8",
            )
            rules = Path(td) / "rules.csv"
            rules.write_text(
                "rule_id,scope,severity,priority,expression,message_template\n"
                "R1,record,ERROR,1,a,b\n",
                encoding="utf-8",
            )
            file_cfg = Path(td) / "file-config.csv"
            file_cfg.write_text("format,delimiter\ndelimited,|\n", encoding="utf-8")

            result = parse_template_with_report(
                mapping,
                rules_input=rules,
                file_config_input=file_cfg,
                derivation_config=DerivationConfig(
                    enabled=True,
                    transaction_code_mode="placeholder",
                    source_field_mode="lineage_hardening",
                    lineage_max_placeholder_ratio=0.01,
                    lineage_max_low_confidence_ratio=0.60,
                ),
            )

            rows = result["payload"]["mappingRows"]
            self.assertEqual(rows[0]["sourceField"], "SRC_ACCOUNT_ID")
            self.assertEqual(rows[0]["sourceLineage"]["confidence"], "medium")
            self.assertEqual(rows[1]["sourceField"], "BALANCE")
            self.assertEqual(rows[1]["sourceLineage"]["confidence"], "low")

            lineage = result["conversion"]["lineage"]
            self.assertEqual(lineage["summary"]["placeholderCount"], 0)
            self.assertEqual(lineage["summary"]["lowConfidenceCount"], 1)
            self.assertEqual(lineage["gates"][0]["status"], "PASS")
            self.assertEqual(lineage["gates"][1]["status"], "PASS")

    def test_lineage_threshold_flags_low_confidence_ratio(self):
        with tempfile.TemporaryDirectory() as td:
            mapping = Path(td) / "mapping.csv"
            mapping.write_text(
                "target_field,data_type\n"
                "ACCOUNT_ID,string\n"
                "BALANCE,numeric\n",
                encoding="utf-8",
            )
            rules = Path(td) / "rules.csv"
            rules.write_text(
                "rule_id,scope,severity,priority,expression,message_template\n"
                "R1,record,ERROR,1,a,b\n",
                encoding="utf-8",
            )
            file_cfg = Path(td) / "file-config.csv"
            file_cfg.write_text("format,delimiter\ndelimited,|\n", encoding="utf-8")

            result = parse_template_with_report(
                mapping,
                rules_input=rules,
                file_config_input=file_cfg,
                derivation_config=DerivationConfig(
                    enabled=True,
                    transaction_code_mode="placeholder",
                    source_field_mode="lineage_hardening",
                    lineage_max_placeholder_ratio=0.01,
                    lineage_max_low_confidence_ratio=0.10,
                ),
            )

            lineage = result["conversion"]["lineage"]
            self.assertEqual(lineage["status"], "WARN")
            self.assertEqual(lineage["gates"][1]["name"], "low_confidence_ratio")
            self.assertEqual(lineage["gates"][1]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
