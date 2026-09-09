import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/ai-comment-labeler/scripts/label_io.py"
SPEC = importlib.util.spec_from_file_location("label_io", SCRIPT)
io = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(io)


class LabelIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dabiao-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = io.read_json(ROOT / "examples/project.json")
        self.row = {"id": "a", "text": "澄露很好用"}
        self.prediction = {
            "id": "a", "stance": "positive", "targets": ["product"],
            "aspects": [], "evidence": [{"source": "text", "quote": "很好用"}],
            "reason": "作者明确认可产品。", "confidence": "high",
            "flags": [], "needs_review": False,
        }

    def run_data(self, rows=None):
        io.write_jsonl(self.root / "input.jsonl", rows or [self.row])
        io.write_json(self.root / "project.json", self.config)
        io.prepare(self.root / "input.jsonl", self.root / "project.json", self.root / "run")
        return self.root / "run"

    def checked(self, rows=None, predictions=None):
        run = self.run_data(rows)
        io.write_jsonl(self.root / "predictions.jsonl", predictions or [self.prediction])
        summary = io.validate(run, self.root / "predictions.jsonl", self.root / "checked", "test-only")
        return summary, io.read_jsonl(self.root / "checked/labels.jsonl")

    def test_complete_flow_and_provenance(self):
        summary, labeled = self.checked()
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["semantic_accuracy"], "not_evaluated")
        self.assertFalse(labeled[0]["review_required"])
        self.assertEqual(labeled[0]["input"], self.row)
        self.assertEqual(labeled[0]["provenance"]["confidence_calibration"], "uncalibrated")
        self.assertEqual(io.read_jsonl(self.root / "checked/review.jsonl", allow_empty=True), [])

    def test_identical_text_is_preserved_by_id(self):
        rows = [self.row, dict(self.row, id="b", context={"parent_comment": "反讽上下文"})]
        prepared = io.batches(io.inputs(rows), self.config, 1, 16000)
        self.assertEqual([chunk["comments"][0]["id"] for chunk in prepared], ["a", "b"])

    def test_duplicate_input_id_is_error(self):
        with self.assertRaisesRegex(ValueError, "duplicate row id"):
            io.inputs([self.row, self.row])

    def test_empty_and_duplicate_key_and_nan_are_errors(self):
        for raw in ('{"id":"a","id":"b"}', '{"x":NaN}', '{"x":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                io.parse(raw)
        (self.root / "empty.jsonl").touch()
        with self.assertRaisesRegex(ValueError, "empty"):
            io.read_jsonl(self.root / "empty.jsonl")

    def test_jsonl_reader_applies_explicit_result_limits(self):
        path = self.root / "expanded.jsonl"
        io.write_jsonl(path, [{"id": "a", "text": "文" * 60}])
        with self.assertRaisesRegex(ValueError, "character limit"):
            io.read_jsonl(path, max_chars=40)
        self.assertEqual(len(io.read_jsonl(path, max_chars=200)), 1)
        with self.assertRaisesRegex(ValueError, "character limit"):
            io.read_jsonl(path, max_chars=200, max_line=20)

    def test_config_and_context_are_strict(self):
        for config in (dict(self.config, aliases="a"), dict(self.config, aspects=[]),
                       dict(self.config, target_brand=""), dict(self.config, api_key="example")):
            with self.subTest(config=config), self.assertRaises(ValueError):
                io.project_config(config)
        for row in (dict(self.row, text=" "), dict(self.row, context={"unknown": "x"}),
                    dict(self.row, context={"title": 3})):
            with self.subTest(row=row), self.assertRaises(ValueError):
                io.inputs([row])

    def test_batch_character_budget_and_order(self):
        rows = [{"id": str(i), "text": "文" * 400} for i in range(6)]
        chunks = io.batches(rows, self.config, 20, 1000)
        self.assertEqual([r["id"] for chunk in chunks for r in chunk["comments"]], [str(i) for i in range(6)])
        self.assertTrue(all(len(io.encode(chunk)) <= 1000 for chunk in chunks))

    def test_oversize_row_fails_without_truncation(self):
        with self.assertRaisesRegex(ValueError, "single comment"):
            io.batches([dict(self.row, text="文" * 2000)], self.config, 20, 1000)

    def test_missing_and_extra_predictions_fail_before_output(self):
        run = self.run_data([self.row, dict(self.row, id="b")])
        io.write_jsonl(self.root / "predictions.jsonl", [self.prediction])
        with self.assertRaisesRegex(ValueError, "missing=1"):
            io.validate(run, self.root / "predictions.jsonl", self.root / "checked", "test")
        self.assertFalse((self.root / "checked").exists())
        io.write_jsonl(self.root / "predictions.jsonl", [self.prediction, dict(self.prediction, id="c")])
        with self.assertRaisesRegex(ValueError, "extra=1"):
            io.validate(run, self.root / "predictions.jsonl", self.root / "checked", "test")

    def test_duplicate_prediction_ids_across_batches_fail(self):
        run = self.run_data()
        for name in ("0001", "0002"):
            io.write_jsonl(run / "predictions" / (name + ".jsonl"), [self.prediction])
        with self.assertRaisesRegex(ValueError, "duplicate row id"):
            io.validate(run, run / "predictions", self.root / "checked", "test")

    def test_shuffled_results_are_joined_by_id_in_input_order(self):
        rows = [self.row, dict(self.row, id="b", text="澄露不好用")]
        second = dict(self.prediction, id="b", stance="negative",
                      evidence=[{"source": "text", "quote": "不好用"}])
        _, labeled = self.checked(rows, [second, self.prediction])
        self.assertEqual([r["id"] for r in labeled], ["a", "b"])
        self.assertEqual(labeled[1]["prediction"]["stance"], "negative")

    def test_fabricated_quote_is_rejected(self):
        pred = dict(self.prediction, evidence=[{"source": "text", "quote": "已经回购了"}])
        with self.assertRaisesRegex(ValueError, "substring"):
            io.check_prediction(pred, self.row, self.config)

    def test_context_alone_is_not_comment_evidence(self):
        row = dict(self.row, context={"parent_comment": "很好用"})
        pred = dict(self.prediction, evidence=[{"source": "parent_comment", "quote": "很好用"}])
        with self.assertRaisesRegex(ValueError, "current comment"):
            io.check_prediction(pred, row, self.config)

    def test_missing_or_blank_evidence_is_rejected(self):
        for evidence in ([], [{"source": "text", "quote": ""}],
                         [{"source": "title", "quote": "不存在"}],
                         [{"source": "other", "quote": "很好用"}]):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                io.check_prediction(dict(self.prediction, evidence=evidence), self.row, self.config)

    def test_unknown_label_field_or_bool_is_rejected(self):
        for change in ({"stance": "spam"}, {"confidence": 0.99}, {"needs_review": 1},
                       {"extra": "x"}, {"targets": []}, {"flags": ["invented"]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                io.check_prediction(dict(self.prediction, **change), self.row, self.config)

    def test_creator_only_cannot_have_brand_polarity(self):
        with self.assertRaisesRegex(ValueError, "in-scope"):
            io.check_prediction(dict(self.prediction, targets=["creator"]), self.row, self.config)

    def test_none_cannot_coexist_with_other_targets(self):
        with self.assertRaisesRegex(ValueError, "coexist"):
            io.check_prediction(dict(self.prediction, targets=["none", "brand"]), self.row, self.config)

    def test_unknown_aspect_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "aspect name"):
            io.check_prediction(dict(self.prediction, aspects=[{"name": "invented", "stance": "positive"}]), self.row, self.config)

    def test_no_attitude_cannot_have_target_aspect_opinions(self):
        pred = dict(self.prediction, stance="no_attitude", aspects=[{"name": "price", "stance": "negative"}])
        with self.assertRaisesRegex(ValueError, "no_attitude"):
            io.check_prediction(pred, self.row, self.config)

    def test_review_signals_cannot_be_overridden_by_false(self):
        variants = [("stance", "uncertain"), ("stance", "mixed"), ("confidence", "medium"),
                    ("confidence", "low"), ("needs_review", True)]
        variants.extend(("flags", [flag]) for flag in io.FLAGS)
        for key, value in variants:
            with self.subTest(key=key, value=value):
                self.assertTrue(io.check_prediction(dict(self.prediction, **{key: value}), self.row, self.config))

    def test_stance_aspect_conflict_routes_review(self):
        pred = dict(self.prediction, aspects=[{"name": "price", "stance": "negative"}])
        self.assertIn("stance_aspect_conflict", io.check_prediction(pred, self.row, self.config))

    def test_tampered_input_or_rules_fail(self):
        run = self.run_data()
        io.write_jsonl(run / "input.jsonl", [dict(self.row, text="已更改")])
        with self.assertRaisesRegex(ValueError, "changed"):
            io.load_run(run)

    def test_tampered_batch_fails(self):
        run = self.run_data()
        io.write_json(run / "batches/0001.json", {"comments": []})
        with self.assertRaisesRegex(ValueError, "batches changed"):
            io.load_run(run)

    def test_existing_output_is_not_overwritten(self):
        _, _ = self.checked()
        result = (self.root / "checked/labels.jsonl").read_bytes()
        with self.assertRaises(FileExistsError):
            io.validate(self.root / "run", self.root / "predictions.jsonl", self.root / "checked", "test")
        self.assertEqual((self.root / "checked/labels.jsonl").read_bytes(), result)

    def test_evaluation_denominators_include_missed_class(self):
        rows = [self.row, dict(self.row, id="b")]
        predictions = [self.prediction, dict(self.prediction, id="b", confidence="low")]
        self.checked(rows, predictions)
        gold = [{"id": "a", "stance": "positive"}, {"id": "b", "stance": "negative"}]
        io.write_jsonl(self.root / "gold.jsonl", gold)
        result = io.evaluate(self.root / "gold.jsonl", self.root / "checked/labels.jsonl")
        self.assertEqual(result["accuracy"], 0.5)
        self.assertAlmostEqual(result["macro_f1_present_labels"], 1 / 3)
        self.assertEqual(result["per_label"]["negative"]["recall"], 0)
        self.assertIsNone(result["per_label"]["neutral"]["f1"])
        self.assertEqual(result["auto_candidate_coverage"], 0.5)
        self.assertEqual(result["auto_candidate_accuracy"], 1)

    def test_evaluation_no_auto_candidate_is_null(self):
        self.checked(predictions=[dict(self.prediction, confidence="low")])
        io.write_jsonl(self.root / "gold.jsonl", [{"id": "a", "stance": "positive"}])
        result = io.evaluate(self.root / "gold.jsonl", self.root / "checked/labels.jsonl")
        self.assertIsNone(result["auto_candidate_accuracy"])

    def test_evaluation_id_mismatch_fails(self):
        self.checked()
        io.write_jsonl(self.root / "gold.jsonl", [{"id": "different", "stance": "positive"}])
        with self.assertRaisesRegex(ValueError, "IDs"):
            io.evaluate(self.root / "gold.jsonl", self.root / "checked/labels.jsonl")

    def test_cli_prepare_then_empty_predictions_fails_clearly(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "prepare", "--input",
                                 str(ROOT / "examples/comments.jsonl"), "--project",
                                 str(ROOT / "examples/project.json"), "--out", str(self.root / "demo")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["labeled"], 0)
        result = subprocess.run([sys.executable, str(SCRIPT), "validate", "--run", str(self.root / "demo"),
                                 "--predictions", str(self.root / "demo/predictions"), "--out",
                                 str(self.root / "checked"), "--model", "test"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no prediction", result.stderr)


if __name__ == "__main__":
    unittest.main()
