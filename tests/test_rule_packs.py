import copy
from pathlib import Path
import tempfile
import unittest

from test_label_io import io, ROOT


class RulePackTests(unittest.TestCase):
    def setUp(self):
        self.config = io.read_json(ROOT / "examples/multidimensional-project.json")
        self.row = {"id": "s1", "document_id": "d1", "segment_index": 1,
                    "is_last_segment": True, "source_kind": "full_text",
                    "text": "担心，准备送检，后来已经检测完，我放心了，仍信任品牌。"}
        self.pred = {"id": "s1", "targets": ["product"], "aspects": [],
                     "labels": {"validity": self.values("valid"), "trust": self.values("support"),
                                "emotion": self.values("relieved"), "intention": []},
                     "behaviors": [], "evidence": self.evidence(), "reason": "虚构的结构测试。",
                     "confidence": "high", "flags": [], "needs_review": False}

    def evidence(self):
        return [{"source": "text", "quote": "已经检测完"}]

    def values(self, *codes):
        return [{"value": code, "evidence": self.evidence()} for code in codes]

    def behavior(self, stage="done", actor="self", scope="unspecified"):
        return {"action": "inspection", "stage": stage, "actor": actor,
                "time_scope": scope, "evidence": self.evidence()}

    def labeled(self, row=None, pred=None):
        row, pred = row or self.row, pred or self.pred
        reasons = io.check_prediction_v2(pred, row, self.config)
        return {"id": row["id"], "input": row, "prediction": pred,
                "review_required": bool(reasons), "review_reasons": reasons}

    def test_existing_examples_are_valid(self):
        io.project_config(self.config)
        io.inputs(io.read_jsonl(ROOT / "examples/note-segments.jsonl"), self.config)
        self.assertEqual(io.check_prediction_v2(self.pred, self.row, self.config), [])

    def test_arbitrary_label_dimension_does_not_require_code_change(self):
        config = copy.deepcopy(self.config)
        config["dimensions"] = {"custom_topic": {
            "title": "自定义话题", "mode": "single", "aggregate": "final",
            "unknown": "U", "review_labels": ["U"],
            "labels": {"X.9": {"name": "自定义标签", "definition": "测试配置可更换。"},
                       "U": {"name": "待定", "definition": "没有依据。"}}}}
        config["denominator"] = None
        io.project_config(config)
        pred = dict(self.pred, labels={"custom_topic": self.values("X.9")})
        self.assertEqual(io.check_prediction_v2(pred, self.row, config), [])

    def test_invalid_configs_fail(self):
        for change in ({"schema_version": True}, {"schema_version": 3}, {"dimensions": {}},
                       {"unit": "anything"}, {"rules": "not an array"},
                       {"denominator": {"dimension": "intention", "include": ["return"]}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                io.project_config(dict(self.config, **change))

    def test_unknown_label_or_missing_dimension_fails(self):
        for dimension in ({"emotion": self.values("invented")}, {}):
            pred = copy.deepcopy(self.pred)
            if dimension:
                pred["labels"].update(dimension)
            else:
                del pred["labels"]["emotion"]
            with self.assertRaises(ValueError):
                io.check_prediction_v2(pred, self.row, self.config)

    def test_single_multi_and_unknown_exclusion(self):
        for dim, codes in (("emotion", []), ("emotion", ["worried", "relieved"]),
                           ("intention", ["unknown", "return"]), ("intention", ["return", "return"])):
            pred = copy.deepcopy(self.pred)
            pred["labels"][dim] = self.values(*codes)
            with self.subTest(dim=dim, codes=codes), self.assertRaises(ValueError):
                io.check_prediction_v2(pred, self.row, self.config)

    def test_per_label_evidence_cannot_rely_on_global_evidence(self):
        for evidence in ([], [{"source": "text", "quote": "不存在的句子"}],
                         [{"source": "title", "quote": "已经检测完"}]):
            pred = copy.deepcopy(self.pred)
            pred["labels"]["emotion"][0]["evidence"] = evidence
            with self.assertRaises(ValueError):
                io.check_prediction_v2(pred, dict(self.row, context={"title": "已经检测完"}), self.config)

    def test_unknown_and_excerpt_force_review(self):
        pred = copy.deepcopy(self.pred)
        pred["labels"]["trust"] = [{"value": "unknown", "evidence": []}]
        reasons = io.check_prediction_v2(pred, dict(self.row, source_kind="excerpt"), self.config)
        self.assertIn("label_trust:unknown", reasons)
        self.assertIn("excerpt_not_full_source", reasons)

    def test_segments_require_contiguous_indices_and_correct_last(self):
        for row in (dict(self.row, segment_index=2), dict(self.row, is_last_segment=False),
                    dict(self.row, segment_index=True), dict(self.row, source_kind="summary")):
            with self.subTest(row=row), self.assertRaises(ValueError):
                io.inputs([row], self.config)
        rows = [dict(self.row, is_last_segment=False), dict(self.row, id="s2", segment_index=2)]
        io.inputs(rows, self.config)
        with self.assertRaisesRegex(ValueError, "one row"):
            io.inputs(rows, dict(self.config, unit="item"))

    def test_behavior_contract_and_evidence(self):
        for change in ({"stage": "finished"}, {"actor": "owner"}, {"time_scope": "tomorrow"},
                       {"action": "invented"}, {"evidence": []}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                io.check_prediction_v2(dict(self.pred, behaviors=[dict(self.behavior(), **change)]),
                                       self.row, self.config)
        reasons = io.check_prediction_v2(dict(self.pred, behaviors=[self.behavior(actor="unspecified")]),
                                        self.row, self.config)
        self.assertIn("behavior_uncertain", reasons)

    def test_final_state_and_ever_seen_not_flattened(self):
        first = copy.deepcopy(self.pred)
        first["labels"]["emotion"] = self.values("worried")
        first["labels"]["intention"] = self.values("return")
        first["behaviors"] = [self.behavior("planned")]
        last = dict(self.pred, id="s2", behaviors=[self.behavior("done")])
        rows = [self.labeled(dict(self.row, is_last_segment=False), first),
                self.labeled(dict(self.row, id="s2", segment_index=2), last)]
        docs, summary = io.rollup_v2(list(reversed(rows)), self.config)
        self.assertEqual(docs[0]["labels"]["emotion"], ["relieved"])
        self.assertEqual(docs[0]["ever_seen"]["emotion"], ["worried", "relieved"])
        self.assertEqual(docs[0]["labels"]["intention"], ["return"])
        self.assertEqual(docs[0]["behaviors"][0]["source_id"], "s2")
        self.assertEqual(summary["behavior_document_counts"]["actual"], {"inspection": 1})
        self.assertEqual(summary["behavior_document_counts"]["intent"], {})

    def test_others_suggestions_and_hypotheticals_not_actual_self(self):
        pred = dict(self.pred, behaviors=[self.behavior("done", "other"),
                    self.behavior("suggested", "other"), self.behavior("hypothetical")])
        _, summary = io.rollup_v2([self.labeled(pred=pred)], self.config)
        self.assertTrue(all(not value for value in summary["behavior_document_counts"].values()))

    def test_time_scopes_preserve_past_actual_and_new_intent(self):
        pred = dict(self.pred, behaviors=[self.behavior(scope="before_event"),
                                         self.behavior("planned", scope="after_event")])
        docs, summary = io.rollup_v2([self.labeled(pred=pred)], self.config)
        self.assertEqual(len(docs[0]["behaviors"]), 2)
        self.assertEqual(summary["behavior_document_counts"], {
            "actual": {"inspection": 1}, "intent": {"inspection": 1}, "after_event_actual": {}})

    def test_denominator_derived_from_documents_not_segments(self):
        excluded = copy.deepcopy(self.pred)
        excluded["id"] = "s2"
        excluded["labels"]["validity"] = self.values("information")
        docs, summary = io.rollup_v2([self.labeled(), self.labeled(
            dict(self.row, id="s2", document_id="d2"), excluded)], self.config)
        self.assertEqual(summary["denominator_documents"], 1)
        self.assertEqual(summary["excluded_documents"], 1)
        self.assertEqual(summary["dimension_distributions"]["emotion"]["counts"], {"relieved": 1})
        self.assertEqual(summary["dimension_distributions"]["emotion"]["rates"], {"relieved": 1})
        _, empty = io.rollup_v2([self.labeled(dict(self.row, id="s2"), excluded)], self.config)
        self.assertEqual(empty["denominator_documents"], 0)
        self.assertEqual(empty["dimension_distributions"]["emotion"]["rates"], {})

    def test_full_round_trip_and_v1_evaluator_rejects_v2(self):
        with tempfile.TemporaryDirectory(prefix="rule-pack-test-") as tmp:
            root = Path(tmp)
            io.write_jsonl(root / "input.jsonl", [self.row])
            io.write_json(root / "project.json", self.config)
            result = io.prepare(root / "input.jsonl", root / "project.json", root / "run")
            self.assertEqual(result["labeled"], 0)
            io.write_jsonl(root / "prediction.jsonl", [self.pred])
            result = io.validate(root / "run", root / "prediction.jsonl", root / "checked", "test-only")
            self.assertEqual(result["documents"], 1)
            self.assertEqual(result["semantic_accuracy"], "not_evaluated")
            self.assertEqual(len(io.read_jsonl(root / "checked/documents.jsonl")), 1)
            io.write_jsonl(root / "gold.jsonl", [{"id": "s1", "stance": "positive"}])
            with self.assertRaisesRegex(ValueError, "v1 stance only"):
                io.evaluate(root / "gold.jsonl", root / "checked/labels.jsonl")


if __name__ == "__main__":
    unittest.main()
