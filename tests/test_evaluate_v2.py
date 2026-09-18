import copy
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

from test_label_io import io, ROOT
from test_event_rules import event_case

spec = importlib.util.spec_from_file_location("evaluate_v2", ROOT / "skills/ai-comment-labeler/scripts/evaluate_v2.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


class EvaluateV2Tests(unittest.TestCase):
    def setUp(self):
        self.project, row, pred = event_case()
        self.rows = [row, dict(row, id="second", document_id="second")]
        self.preds = [pred, dict(copy.deepcopy(pred), id="second")]
        self.gold = {"reference_kind": "development", "reference_note": "结构计分测试，不是人类真值",
                     "project_sha256": io.digest(io.encode(self.project)),
                     "input_sha256": io.digest(io.encode(self.rows)), "rows": []}
        for r, p in zip(self.rows, self.preds):
            self.gold["rows"].append({"id": r["id"], "labels": {d: [v["value"] for v in values]
                                      for d, values in p["labels"].items()},
                                      "behaviors": [{k: b[k] for k in ev.BEHAVIOR_KEYS} |
                                                    {"event_relation": b["event_link"]["relation"]}
                                                    for b in p["behaviors"]]})

    def results(self):
        return [{"id": r["id"], "input": r, "prediction": p, "review_required": False,
                 "provenance": {"project_sha256": self.gold["project_sha256"]}}
                for r, p in zip(self.rows, self.preds)]

    def score(self, results=None):
        return ev.evaluate(self.project, self.rows, self.gold, self.results() if results is None else results)

    def test_exact_scores_and_declared_reference_scope(self):
        score = self.score()
        self.assertEqual(score["all_dimensions_exact_match_accuracy"], 1)
        self.assertEqual(score["behaviors"]["micro_f1"], 1)
        self.assertEqual(score["reference_kind"], "development")
        self.assertEqual(score["model_ids"], ["unspecified"])
        self.assertEqual(score["result_sha256"], io.digest(io.encode(self.results())))
        self.assertIn("not_verified_business_accuracy", score["scope"])

    def test_wrong_dimension_and_stage_count_separately(self):
        self.preds[1]["labels"]["brand_concern"][0]["value"] = "not_expressed"
        self.preds[1]["behaviors"][0]["stage"] = "planned"
        score = self.score()
        self.assertEqual(score["dimensions"]["brand_concern"]["exact_match_accuracy"], .5)
        self.assertEqual(score["dimensions"]["event_relevance"]["exact_match_accuracy"], 1)
        self.assertEqual(score["behaviors"]["exact_match_accuracy"], .5)
        self.assertEqual(score["behaviors"]["micro_f1"], .5)
        self.assertEqual({m["field"] for m in score["mismatches"]}, {"labels.brand_concern", "behaviors"})

    def test_wrong_event_cause_is_not_hidden_by_action_match(self):
        self.preds[1]["behaviors"][0]["event_link"].update(relation="unknown", evidence=[])
        score = self.score()
        self.assertEqual(score["behaviors"]["exact_match_accuracy"], .5)
        self.assertEqual(score["auto_candidate_coverage"], .5)

    def test_missing_rows_stay_in_denominator(self):
        score = self.score(self.results()[:1])
        self.assertEqual(score["missing_ids"], ["second"])
        self.assertEqual(score["prediction_coverage"], .5)
        self.assertEqual(score["dimensions"]["event_relevance"]["exact_match_accuracy"], .5)
        self.assertAlmostEqual(score["dimensions"]["event_relevance"]["micro_f1"], 2 / 3)

    def test_error_and_invalid_output_count_in_denominator(self):
        for kind in ("empty", "illegal"):
            results = self.results()
            if kind == "empty":
                results[1].update(status="error", prediction=None)
            else:
                results[1]["prediction"] = dict(self.preds[1], needs_review="false")
            score = self.score(results)
            self.assertEqual(score["error_ids"], ["second"])
            self.assertEqual(score["all_dimensions_exact_match_accuracy"], .5)

    def test_zero_automatic_is_null_accuracy(self):
        results = self.results()
        for result in results:
            result["review_required"] = True
        score = self.score(results)
        self.assertEqual(score["auto_candidate_coverage"], 0)
        self.assertIsNone(score["dimensions"]["event_relevance"]["auto_candidate_exact_match_accuracy"])

    def test_shared_result_format_supported(self):
        results = self.results()
        for result in results:
            del result["review_required"]
            result.update(status="candidate", review_details=[])
        self.assertEqual(self.score(results)["prediction_coverage"], 1)

    def test_reference_input_rule_and_result_mismatches_rejected(self):
        for kind in ("reference_input", "reference_project", "result_input", "result_project", "duplicate", "extra"):
            gold, results = copy.deepcopy(self.gold), copy.deepcopy(self.results())
            if kind == "reference_input":
                gold["input_sha256"] = "wrong"
            elif kind == "reference_project":
                gold["project_sha256"] = "wrong"
            elif kind == "result_input":
                results[0]["input"]["text"] = "different context"
            elif kind == "result_project":
                results[0]["provenance"]["project_sha256"] = "other"
            elif kind == "duplicate":
                results.append(results[0])
            else:
                results.append(dict(results[0], id="foreign"))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                ev.evaluate(self.project, self.rows, gold, results)

    def test_gold_cannot_drop_difficult_ids_or_use_unknown_labels(self):
        for rows in (self.gold["rows"][:1], [dict(r, labels={}) for r in self.gold["rows"]]):
            with self.assertRaises(ValueError):
                ev.evaluate(self.project, self.rows, dict(self.gold, rows=rows), self.results())

    def test_multi_set_metrics_and_empty_output_not_missing(self):
        metrics = ev.set_metrics([{ "a", "b"}, set()], [{"a"}, set()], {"a", "b", "unused"})
        self.assertEqual(metrics["exact_match_accuracy"], .5)
        self.assertAlmostEqual(metrics["micro_f1"], 2 / 3)
        self.assertIsNone(metrics["per_label"]["unused"]["f1"])
        missing = ev.set_metrics([set()], [None], set())
        self.assertEqual(missing["exact_match_accuracy"], 0)
        self.assertIsNone(missing["micro_f1"])

    def test_empty_result_file_does_not_yield_perfect_empty_actions(self):
        for row in self.gold["rows"]:
            row["behaviors"] = []
        score = self.score([])
        self.assertEqual(score["behaviors"]["exact_match_accuracy"], 0)
        self.assertEqual(score["prediction_coverage"], 0)

    def test_cli_runs_and_rejects_mismatch(self):
        with tempfile.TemporaryDirectory(prefix="v2-eval-test-") as tmp:
            folder = Path(tmp)
            io.write_json(folder / "project.json", self.project)
            io.write_json(folder / "gold.json", self.gold)
            io.write_jsonl(folder / "input.jsonl", self.rows)
            io.write_jsonl(folder / "labels.jsonl", self.results())
            command = [sys.executable, str(ROOT / "skills/ai-comment-labeler/scripts/evaluate_v2.py")]
            for flag, file in (("project", "project.json"), ("input", "input.jsonl"), ("gold", "gold.json"), ("labels", "labels.jsonl")):
                command += ["--" + flag, str(folder / file)]
            output = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(output.returncode, 0, output.stderr)
            self.assertEqual(io.parse(output.stdout)["total"], 2)
            io.write_json(folder / "gold.json", dict(self.gold, input_sha256="wrong"))
            output = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(output.returncode, 1)
            self.assertIn("reference/input mismatch", output.stderr)

    def test_non_event_multi_project_full_flow(self):
        project = copy.deepcopy(self.project)
        del project["event"]
        project["dimensions"]["brand_concern"]["mode"] = "multi"
        gold = copy.deepcopy(self.gold)
        gold["project_sha256"] = io.digest(io.encode(project))
        results = copy.deepcopy(self.results())
        for ref, result in zip(gold["rows"], results):
            ref["behaviors"] = []
            result["prediction"]["behaviors"] = []
            result["provenance"]["project_sha256"] = gold["project_sha256"]
        # Second record has two valid reference labels; returning one is not an exact match.
        gold["rows"][1]["labels"]["brand_concern"] = ["expressed", "not_expressed"]
        score = ev.evaluate(project, self.rows, gold, results)
        self.assertEqual(score["dimensions"]["brand_concern"]["mode"], "multi")
        self.assertEqual(score["dimensions"]["brand_concern"]["exact_match_accuracy"], .5)
        self.assertNotIn("confusion_matrix", score["dimensions"]["brand_concern"])
        self.assertEqual(score["behaviors"]["exact_match_accuracy"], 1)
        self.assertIsNone(score["behaviors"]["micro_f1"])
