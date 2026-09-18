import copy
import unittest

from test_label_io import io, ROOT
from test_shared_labeler import shared


def event_case():
    project = io.read_json(ROOT / "examples/event-project.json")
    row = io.read_jsonl(ROOT / "examples/event-comments.jsonl")[4]
    evidence = [{"source": "text", "quote": row["text"]}]
    behavior = {"action": "switch_away", "actor": "self", "stage": "done", "time_scope": "after_event",
                "evidence": evidence, "event_link": {"event_id": project["event"]["id"],
                                                       "relation": "caused_by", "evidence": evidence}}
    prediction = {"id": row["id"], "targets": ["product"], "aspects": [],
                  "labels": {"event_relevance": [{"value": "related", "evidence": evidence}],
                             "brand_concern": [{"value": "expressed", "evidence": evidence}]},
                  "behaviors": [behavior], "evidence": evidence, "reason": "结构测试，不是语义评测。",
                  "confidence": "high", "flags": [], "needs_review": False,
                  "ai_review": {"checked_fields": sorted(io.review_fields(project)), "issues": []}}
    return project, row, prediction


class EventRulesTests(unittest.TestCase):
    def setUp(self):
        self.project, self.row, self.pred = event_case()

    def checked(self, row=None, pred=None):
        row, pred = row or self.row, pred or self.pred
        reasons = io.check_prediction_v2(pred, row, self.project)
        return {"id": row["id"], "input": row, "prediction": pred, "review_required": bool(reasons)}

    def test_event_template_and_caused_action_valid(self):
        io.project_config(self.project)
        io.inputs(io.read_jsonl(ROOT / "examples/event-comments.jsonl"), self.project)
        self.assertEqual(io.check_prediction_v2(self.pred, self.row, self.project), [])

    def test_event_config_cannot_treat_unknown_as_related(self):
        for change in ({"related_labels": ["unknown"]}, {"relevance_dimension": "missing"},
                       {"related_labels": []}):
            project = copy.deepcopy(self.project)
            project["event"].update(change)
            with self.assertRaises(ValueError):
                io.project_config(project)

    def test_behavior_requires_its_own_cause_evidence(self):
        self.pred["behaviors"][0]["event_link"]["evidence"] = []
        with self.assertRaisesRegex(ValueError, "evidence"):
            io.check_prediction_v2(self.pred, self.row, self.project)
        self.pred["behaviors"][0]["event_link"]["evidence"] = [{"source": "text", "quote": "虚构原因"}]
        with self.assertRaisesRegex(ValueError, "substring"):
            io.check_prediction_v2(self.pred, self.row, self.project)

    def test_context_only_cause_is_not_enough(self):
        self.row["context"] = {"title": "电池事件"}
        self.pred["behaviors"][0]["event_link"]["evidence"] = [{"source": "title", "quote": "电池事件"}]
        with self.assertRaisesRegex(ValueError, "current comment"):
            io.check_prediction_v2(self.pred, self.row, self.project)

    def test_wrong_event_or_before_event_cause_rejected(self):
        for change in ("event_id", "time_scope", "missing_link", "relevance"):
            pred = copy.deepcopy(self.pred)
            if change == "event_id":
                pred["behaviors"][0]["event_link"]["event_id"] = "other-event"
            elif change == "time_scope":
                pred["behaviors"][0]["time_scope"] = "before_event"
            elif change == "missing_link":
                del pred["behaviors"][0]["event_link"]
            else:
                pred["labels"]["event_relevance"][0]["value"] = "unrelated"
            with self.subTest(change=change), self.assertRaises(ValueError):
                io.check_prediction_v2(pred, self.row, self.project)

    def test_unknown_cause_forces_only_behavior_review(self):
        link = self.pred["behaviors"][0]["event_link"]
        link.update(relation="unknown", evidence=[])
        reasons = io.check_prediction_v2(self.pred, self.row, self.project)
        details, status = io.review_details_v2(self.pred, self.row, self.project, reasons)
        self.assertEqual(reasons, ["event_cause_unknown"])
        self.assertEqual(details[0]["field"], "behaviors[0]")
        self.assertEqual(status["behaviors"], "review")
        self.assertEqual(status["labels.brand_concern"], "candidate")

    def test_after_event_not_automatically_event_caused(self):
        self.pred["behaviors"][0]["event_link"].update(relation="unknown", evidence=[])
        _, summary = io.rollup_v2([self.checked()], self.project)
        counts = summary["behavior_document_counts"]
        self.assertEqual(counts["after_event_actual"], {"switch_away": 1})
        self.assertEqual(counts["event_caused_actual"], {})

    def test_considering_planned_and_other_not_actual(self):
        for stage, actor in (("considering", "self"), ("planned", "self"), ("done", "other")):
            pred = copy.deepcopy(self.pred)
            pred["behaviors"][0].update(stage=stage, actor=actor)
            _, summary = io.rollup_v2([self.checked(pred=pred)], self.project)
            counts = summary["behavior_document_counts"]
            self.assertEqual(counts["actual"], {})
            self.assertEqual(counts["event_caused_actual"], {})
            self.assertEqual(counts["intent"], {"switch_away": 1} if actor == "self" else {})

    def test_switch_and_return_directions_both_survive(self):
        back = copy.deepcopy(self.pred["behaviors"][0])
        back["action"] = "return_to_brand"
        back["event_link"]["relation"] = "not_caused_by"
        self.pred["behaviors"].append(back)
        docs, summary = io.rollup_v2([self.checked()], self.project)
        self.assertEqual({b["action"] for b in docs[0]["behaviors"]}, {"switch_away", "return_to_brand"})
        self.assertEqual(summary["behavior_document_counts"]["event_caused_actual"], {"switch_away": 1})

    def test_different_causes_do_not_collapse_across_segments(self):
        self.project["unit"] = "segment"
        earlier = copy.deepcopy(self.pred)
        earlier["behaviors"][0]["event_link"].update(relation="unknown", evidence=[])
        later = dict(self.pred, id="later")
        docs, _ = io.rollup_v2([
            self.checked(dict(self.row, is_last_segment=False), earlier),
            self.checked(dict(self.row, id="later", segment_index=2), later)], self.project)
        self.assertEqual(len(docs[0]["behaviors"]), 2)
        self.assertEqual({b["event_link"]["relation"] for b in docs[0]["behaviors"]}, {"unknown", "caused_by"})

    def test_unknown_dimension_cannot_bypass_offline_review(self):
        self.project["dimensions"]["brand_concern"]["review_labels"] = []
        self.pred["labels"]["brand_concern"] = [{"value": "unknown", "evidence": []}]
        reasons = io.check_prediction_v2(self.pred, self.row, self.project)
        self.assertIn("label_brand_concern:unknown", reasons)

    def test_shared_entry_uses_same_event_validation(self):
        self.pred["behaviors"][0]["event_link"]["evidence"] = []
        labeler = shared.SharedLabeler(self.project, lambda _: {"type": "prediction", "prediction": self.pred},
                                       model_id="fixed-structural-test")
        self.assertEqual(labeler.label(self.row)["status"], "error")
        self.pred["behaviors"][0]["event_link"]["relation"] = "unknown"
        self.assertEqual(labeler.label(self.row)["status"], "review")

    def test_cause_image_evidence_is_also_reviewed(self):
        self.row["context"] = {"image_ocr": "事件通知"}
        link = self.pred["behaviors"][0]["event_link"]
        link["evidence"] = link["evidence"] + [{"source": "image_ocr", "quote": "事件通知"}]
        labeler = shared.SharedLabeler(self.project, lambda _: {"type": "prediction", "prediction": self.pred},
                                       model_id="fixed-structural-test")
        result = labeler.label(self.row)
        self.assertEqual(result["status"], "review")
        self.assertTrue(any(d["reason"] == "derived_image_evidence" for d in result["review_details"]))
