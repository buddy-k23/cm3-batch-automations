import json
import tempfile
import unittest
from pathlib import Path

from tools.e2e_runner import DETERMINISTIC_TIMESTAMP, run_pipeline
from tools.template_parser import DerivationConfig
from tools.generate_contracts import generate


class E2ERunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.mapping = self.root / "examples/templates/mapping-template.csv"
        self.rules = self.root / "examples/templates/rules-template.csv"
        self.file_cfg = self.root / "examples/templates/file-config-template.csv"

    def test_run_pipeline_generates_all_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "out"
            result = run_pipeline(
                input_path=self.mapping,
                out_dir=out_dir,
                rules_input=self.rules,
                file_config_input=self.file_cfg,
                version="0.2.0",
                owner="qa-owner",
                deterministic=True,
            )
            self.assertEqual(result["status"], "SUCCESS")
            for key in ["canonical", "mapping", "rules", "conversion", "lineage", "promotionEvidence", "summary", "detailCsv", "telemetry"]:
                self.assertTrue(Path(result["artifacts"][key]).exists(), key)

            conversion = json.loads((out_dir / "conversion-report.json").read_text(encoding="utf-8"))
            self.assertEqual(conversion["generatedAt"], DETERMINISTIC_TIMESTAMP)
            self.assertEqual(conversion["input"], "template-ingest.json")

            summary = json.loads((out_dir / "summary-report.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["runId"], result["runId"])
            self.assertEqual(summary["status"], "SUCCESS")

    def test_pipeline_is_deterministic_for_same_input(self):
        with tempfile.TemporaryDirectory() as td:
            out1 = Path(td) / "run1"
            out2 = Path(td) / "run2"

            first = run_pipeline(
                input_path=self.mapping,
                out_dir=out1,
                rules_input=self.rules,
                file_config_input=self.file_cfg,
                version="0.2.0",
                owner="qa-owner",
                deterministic=True,
            )
            second = run_pipeline(
                input_path=self.mapping,
                out_dir=out2,
                rules_input=self.rules,
                file_config_input=self.file_cfg,
                version="0.2.0",
                owner="qa-owner",
                deterministic=True,
            )

            self.assertEqual(first["runId"], second["runId"])
            for name in [
                "template-ingest.json",
                "mapping.json",
                "rules.json",
                "conversion-report.json",
                "summary-report.json",
                "telemetry.json",
                "detail-violations.csv",
                "promotion-evidence.json",
                "lineage-report.json",
            ]:
                self.assertEqual(
                    (out1 / name).read_text(encoding="utf-8"),
                    (out2 / name).read_text(encoding="utf-8"),
                    name,
                )

    def test_pipeline_derivation_warnings_flow_to_conversion_report(self):
        with tempfile.TemporaryDirectory() as td:
            mapping = Path(td) / "mapping.csv"
            mapping.write_text("target_field,data_type\nACCOUNT_ID,string\n", encoding="utf-8")
            rules = Path(td) / "rules.csv"
            rules.write_text(
                "rule_id,scope,severity,priority,expression,message_template\n"
                "R1,record,ERROR,1,a,b\n",
                encoding="utf-8",
            )
            file_cfg = Path(td) / "file-config.csv"
            file_cfg.write_text("format,delimiter\ndelimited,|\n", encoding="utf-8")
            out_dir = Path(td) / "out"
            result = run_pipeline(
                input_path=mapping,
                out_dir=out_dir,
                rules_input=rules,
                file_config_input=file_cfg,
                version="0.2.0",
                owner="qa-owner",
                deterministic=True,
                derivation_config=DerivationConfig(enabled=True, transaction_code_mode="placeholder", source_field_mode="target_field"),
            )
            self.assertEqual(result["status"], "WARN")
            conversion = json.loads((out_dir / "conversion-report.json").read_text(encoding="utf-8"))
            self.assertEqual(conversion["status"], "WARN")
            self.assertEqual(conversion["summary"]["warnings"], 2)
            self.assertEqual(len(conversion["warnings"]), 2)

    def test_mapping_only_workbook_generates_placeholder_rule_pack(self):
        with tempfile.TemporaryDirectory() as td:
            mapping = Path(td) / "mapping.csv"
            mapping.write_text(
                "target_field,data_type\n"
                "ACCOUNT_ID,string\n",
                encoding="utf-8",
            )
            file_cfg = Path(td) / "file-config.csv"
            file_cfg.write_text("format,delimiter\ndelimited,|\n", encoding="utf-8")
            out_dir = Path(td) / "out"

            result = run_pipeline(
                input_path=mapping,
                out_dir=out_dir,
                rules_input=None,
                file_config_input=file_cfg,
                version="0.2.0",
                owner="qa-owner",
                deterministic=True,
                derivation_config=DerivationConfig(enabled=True, transaction_code_mode="sheet_name", source_field_mode="lineage_hardening"),
            )

            self.assertEqual(result["status"], "WARN")
            conversion = json.loads((out_dir / "conversion-report.json").read_text(encoding="utf-8"))
            self.assertEqual(conversion["status"], "WARN")
            self.assertEqual(conversion["summary"]["errors"], 0)
            self.assertEqual(conversion["summary"]["ruleRows"], 0)
            self.assertEqual(conversion["summary"]["generatedRules"], 1)

            rules_payload = json.loads((out_dir / "rules.json").read_text(encoding="utf-8"))
            self.assertEqual(len(rules_payload["rules"]), 1)
            self.assertEqual(rules_payload["rules"][0]["ruleId"], "RULEPACK_PLACEHOLDER_INFO")

    def test_validation_engine_emits_real_violations_and_counts(self):
        with tempfile.TemporaryDirectory() as td:
            mapping = Path(td) / "mapping.csv"
            mapping.write_text(
                "transaction_code,target_field,source_field,data_type,required,default_value\n"
                "A01,ACCOUNT_ID,SRC_ACCOUNT_ID,string,Y,A\n"
                "A01,SEQUENCE_NUMBER,SRC_SEQ,numeric,Y,5\n"
                "A01,TOTAL_COUNT,SRC_TOTAL,numeric,N,1\n",
                encoding="utf-8",
            )
            rules = Path(td) / "rules.csv"
            rules.write_text(
                "rule_id,rule_name,scope,severity,priority,group_by,expression,message_template,enabled\n"
                "FLD_TXN_CHECK,Txn code check,field,ERROR,10,,transactionCode == 'B01',transaction code must be B01,Y\n"
                "GRP_COUNT_MIN,Group count minimum,group,ERROR,20,transaction_code,COUNT(*) > 10,group count must be > 10,Y\n"
                "FIL_COUNT_MATCH,File count match,file,ERROR,30,,header.total_count == detail.count,header mismatch,Y\n",
                encoding="utf-8",
            )
            file_cfg = Path(td) / "file-config.csv"
            file_cfg.write_text(
                "format,delimiter,header_enabled,header_total_count_field\n"
                "delimited,|,Y,TOTAL_COUNT\n",
                encoding="utf-8",
            )

            out_dir = Path(td) / "out"
            result = run_pipeline(
                input_path=mapping,
                out_dir=out_dir,
                rules_input=rules,
                file_config_input=file_cfg,
                version="0.2.0",
                owner="qa-owner",
                deterministic=True,
            )

            self.assertEqual(result["status"], "FAILED")
            summary = json.loads((out_dir / "summary-report.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["summary"]["failed"], 4)
            self.assertEqual(summary["summary"]["validated"], 5)
            telemetry = json.loads((out_dir / "telemetry.json").read_text(encoding="utf-8"))
            by_rule = {r["ruleId"]: r["failCount"] for r in telemetry["quality"]["byRule"]}
            self.assertEqual(by_rule["FLD_TXN_CHECK"], 3)
            self.assertEqual(by_rule["GRP_COUNT_MIN"], 1)
            self.assertEqual(by_rule["FIL_COUNT_MATCH"], 0)

            detail = (out_dir / "detail-violations.csv").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(detail), 5)

    def test_promotion_gate_enabled_fails_on_completeness_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "out"
            result = run_pipeline(
                input_path=self.mapping,
                out_dir=out_dir,
                rules_input=self.rules,
                file_config_input=self.file_cfg,
                version="0.2.0",
                owner="qa-owner",
                deterministic=True,
                promotion_gate_enabled=True,
                template_completeness_ba=90.0,
            )

            self.assertEqual(result["status"], "FAIL")
            evidence = json.loads((out_dir / "promotion-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["evaluation"]["decision"], "FAIL")
            self.assertTrue(any(r["code"] == "BA_COMPLETENESS_BELOW_MIN" for r in evidence["evaluation"]["reasons"]))

    def test_promotion_gate_enabled_passes_when_policy_satisfied(self):
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "out"

            result = run_pipeline(
                input_path=self.mapping,
                out_dir=out_dir,
                rules_input=self.rules,
                file_config_input=self.file_cfg,
                version="0.2.0",
                owner="qa-owner",
                deterministic=True,
                promotion_gate_enabled=True,
                template_completeness_ba=100.0,
                template_completeness_qa=100.0,
                required_columns_completeness=100.0,
            )

            self.assertEqual(result["status"], "PASS")
            evidence = json.loads((out_dir / "promotion-evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["evaluation"]["decision"], "PASS")


class GenerateContractsTests(unittest.TestCase):
    def test_generate_accepts_overrides(self):
        root = Path(__file__).resolve().parents[1]
        canonical = root / "examples/canonical-ingest.sample.json"
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            report = generate(
                input_path=canonical,
                out_dir=out,
                version="0.1.0",
                owner="owner",
                generated_at="2000-01-01T00:00:00+00:00",
                input_ref="canonical-ingest.sample.json",
            )
            self.assertEqual(report["generatedAt"], "2000-01-01T00:00:00+00:00")
            self.assertEqual(report["input"], "canonical-ingest.sample.json")
            self.assertTrue((out / "report.json").exists())
            self.assertTrue((out / "telemetry.json").exists())


if __name__ == "__main__":
    unittest.main()
