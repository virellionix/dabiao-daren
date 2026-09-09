import copy
import importlib.util
from unittest.mock import Mock
import unittest

from test_label_io import io, ROOT

spec = importlib.util.spec_from_file_location(
    "shared_labeler", ROOT / "skills/ai-comment-labeler/scripts/shared_labeler.py")
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)


class SharedEntryTests(unittest.TestCase):
    def setUp(self):
        self.project = io.read_json(ROOT / "examples/content-project.json")
        self.row = shared.adapt_record({"评论ID": "c1", "评论内容": "这款用了一个月，音质很好。"},
                                       id_column="评论ID", text_column="评论内容")
        self.evidence = [{"source": "text", "quote": "这款用了一个月，音质很好"}]
        self.pred = {"id": "c1", "targets": ["product"], "aspects": [],
                     "labels": {"purpose": [{"value": "feedback", "evidence": self.evidence}],
                                "stance": [{"value": "positive", "evidence": self.evidence}]},
                     "behaviors": [], "evidence": self.evidence, "reason": "虚构结构测试。",
                     "confidence": "high", "flags": [], "needs_review": False}
        self.final = {"type": "prediction", "prediction": self.pred}
        self.request = {"type": "context_request", "sources": ["post_text"], "reason": "需明确产品指代。"}
        self.catalog = [{"target": "星舟", "aliases": ["星舟耳机"], "is_own": True},
                        {"target": "银湾", "aliases": ["银湾一号"], "is_own": False}]

    def labeler(self, model=None, resolver=None, project=None):
        return shared.SharedLabeler(project or self.project, model or Mock(return_value=self.final),
                                    model_id="offline-fixed-mock", context_resolver=resolver)

    def test_legacy_columns_preserve_text_and_context(self):
        record = shared.adapt_record(
            {"id": "n1", "正文": "原文", "图转写": "图内原文", "作者信息": "作者简介"},
            id_column="id", text_column="正文",
            context_columns={"image_ocr": "图转写", "author_profile": "作者信息"}, source_kind="excerpt")
        io.inputs([record], self.project)
        self.assertEqual(record["context"], {"image_ocr": "图内原文", "author_profile": "作者简介"})
        self.assertEqual(record["source_kind"], "excerpt")
        self.assertEqual(record["text"], "原文")

    def test_invalid_source_not_coerced_to_text(self):
        for value in (None, float("nan"), 23, ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                shared.adapt_record({"id": "n1", "正文": value}, id_column="id", text_column="正文")

    def test_resolves_target_from_owned_catalog_only(self):
        del self.project["target_brand"]
        self.project["aliases"] = []
        project, scope = shared.resolve_project(self.project, self.catalog)
        self.assertEqual(project["target_brand"], "星舟")
        self.assertEqual(project["aliases"], ["星舟耳机"])
        self.assertEqual(scope["source"], "explicit_catalog_ownership")
        self.assertNotIn("target_brand", self.project)

    def test_explicit_project_and_catalog_conflict_rejected(self):
        self.project["target_brand"] = "银湾"
        with self.assertRaisesRegex(ValueError, "conflict"):
            shared.resolve_project(self.project, self.catalog)

    def test_ambiguous_target_or_string_boolean_rejected(self):
        del self.project["target_brand"]
        for catalog in ([], [dict(self.catalog[0], is_own="false")],
                        [dict(item, is_own=True) for item in self.catalog]):
            with self.subTest(catalog=catalog), self.assertRaises(ValueError):
                shared.resolve_project(self.project, catalog)

    def test_valid_direct_prediction_one_call_no_retrieval(self):
        model, resolver = Mock(return_value=self.final), Mock()
        result = self.labeler(model, resolver).label(self.row, context_refs={"post_text": "n1"})
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["provenance"]["semantic_accuracy"], "not_measured")
        resolver.assert_not_called()
        self.assertIn("policy", model.call_args.args[0])

    def test_context_fetch_continuation_and_trace(self):
        model = Mock(side_effect=[self.request, self.final])
        resolver = Mock(side_effect=lambda bound: dict(bound, status="ok", text="星舟耳机体验。"))
        result = self.labeler(model, resolver).label(self.row, context_refs={"post_text": "n1"})
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(result["input"]["context"]["post_text"], "星舟耳机体验。")
        self.assertNotIn("post_text", self.row["context"])
        resolver.assert_called_once_with({"kind": "post_text", "record_id": "c1", "source_ref": "n1"})
        self.assertEqual(result["context_trace"][0]["text_sha256"], io.digest("星舟耳机体验。"))
        second = model.call_args.args[0]
        self.assertFalse(second["can_request_context"])
        self.assertEqual(second["history"], [self.request])

    def test_foreign_context_is_rejected(self):
        for change in ({"record_id": "other"}, {"source_ref": "other"}, {"kind": "parent_comment"}):
            resolver = Mock(side_effect=lambda bound: dict(dict(bound, status="ok", text="异帖内容"), **change))
            result = self.labeler(Mock(side_effect=[self.request, self.final]), resolver).label(
                self.row, context_refs={"post_text": "n1"})
            self.assertEqual(result["status"], "review")
            self.assertEqual(result["context_trace"][0]["status"], "invalid_or_failed")
            self.assertNotIn("post_text", result["input"]["context"])

    def test_denied_unavailable_not_found_context_remains_review(self):
        for status in ("permission_denied", "not_found", "unavailable"):
            with self.subTest(status=status):
                resolver = Mock(side_effect=lambda bound: dict(bound, status=status))
                result = self.labeler(Mock(side_effect=[self.request, self.final]), resolver).label(
                    self.row, context_refs={"post_text": "n1"})
                self.assertEqual(result["status"], "review")
                self.assertEqual(result["review_details"][0]["reason"], "context_" + status)

    def test_no_bound_reference_does_not_call_resolver(self):
        resolver = Mock()
        result = self.labeler(Mock(side_effect=[self.request, self.final]), resolver).label(self.row)
        resolver.assert_not_called()
        self.assertEqual(result["status"], "review")

    def test_provided_context_not_fetched_again(self):
        resolver = Mock()
        row = dict(self.row, context={"post_text": "已提供的原文"})
        result = self.labeler(Mock(side_effect=[self.request, self.final]), resolver).label(row)
        resolver.assert_not_called()
        self.assertEqual(result["context_trace"], [{"kind": "post_text", "status": "already_provided"}])

    def test_context_request_cannot_supply_arbitrary_url(self):
        request = dict(self.request, url="https://invalid.example/unrelated")
        resolver = Mock()
        result = self.labeler(Mock(return_value=request), resolver).label(self.row)
        self.assertEqual(result["status"], "error")
        resolver.assert_not_called()

    def test_repeated_context_request_stops_after_two_calls(self):
        model = Mock(return_value=self.request)
        result = self.labeler(model).label(self.row)
        self.assertEqual(result["status"], "review")
        self.assertIsNone(result["prediction"])
        self.assertEqual(model.call_count, 2)

    def test_context_failure_and_size_limits_do_not_retry(self):
        for resolver in (Mock(side_effect=RuntimeError("secret must not leak")),
                         Mock(side_effect=lambda b: dict(b, status="ok", text="x" * 12001))):
            result = self.labeler(Mock(side_effect=[self.request, self.final]), resolver).label(
                self.row, context_refs={"post_text": "n1"})
            self.assertEqual(result["status"], "review")
            self.assertEqual(resolver.call_count, 1)
            self.assertNotIn("secret", io.encode(result))

    def test_invalid_prediction_never_turns_into_neutral(self):
        wrong_sentiment = copy.deepcopy(self.pred)
        wrong_sentiment["labels"]["stance"][0]["value"] = "无法判断"
        for pred in (dict(self.pred, needs_review="false"), wrong_sentiment,
                     dict(self.pred, labels={}), dict(self.pred, aspects=None),
                     dict(self.pred, behaviors=[None]), dict(self.pred, id="other")):
            with self.subTest(pred=pred):
                labeler = self.labeler(Mock(return_value={"type": "prediction", "prediction": pred}))
                result = labeler.label(self.row)
                self.assertEqual(result["status"], "error")
                output = labeler.export_columns(result, {"stance": "品牌态度"})
                self.assertIsNone(output["品牌态度"])
                self.assertEqual(output["打标状态"], "error")

    def test_non_json_duplicate_keys_and_invalid_types_fail(self):
        for answer in (None, [], 3, "not JSON", '{"type":"prediction","type":"context_request"}',
                       {"type": "prediction", "prediction": float("nan")}):
            with self.subTest(answer=answer):
                result = self.labeler(Mock(return_value=answer)).label(self.row)
                self.assertEqual(result["status"], "error")

    def test_model_error_no_retry_and_no_fabricated_label(self):
        model = Mock(side_effect=RuntimeError("secret"))
        result = self.labeler(model).label(self.row)
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["prediction"])
        self.assertEqual(model.call_count, 1)
        self.assertNotIn("secret", io.encode(result))

    def test_unknown_cannot_bypass_review_with_high_confidence(self):
        self.project["dimensions"]["stance"]["review_labels"] = []
        self.pred["labels"]["stance"] = [{"value": "unknown", "evidence": []}]
        result = self.labeler().label(self.row)
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["field_status"]["labels.stance"], "review")
        self.assertEqual(result["field_status"]["labels.purpose"], "candidate")

    def test_image_derived_evidence_keeps_review(self):
        self.row["context"]["image_ocr"] = "星舟耳机"
        self.pred["labels"]["stance"][0]["evidence"] = self.evidence + [
            {"source": "image_ocr", "quote": "星舟耳机"}]
        result = self.labeler().label(self.row)
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["field_status"]["labels.stance"], "review")

    def test_export_retains_legacy_columns_and_review_status(self):
        labeler = self.labeler()
        output = labeler.export_columns(labeler.label(self.row), {"purpose": "判断标签", "stance": "品牌态度"})
        self.assertEqual(output, {"判断标签": "使用反馈", "品牌态度": "正面", "打标状态": "candidate", "复核原因": ""})
        with self.assertRaises(ValueError):
            labeler.export_columns(labeler.label(self.row), {"stance": "打标状态"})

    def test_project_version_cannot_drift_mid_run_or_cross_export(self):
        labeler = self.labeler()
        result = labeler.label(self.row)
        labeler.project["rule_version"] = "2.0"
        with self.assertRaises(ValueError):
            labeler.label(self.row)
        with self.assertRaises(ValueError):
            labeler.export_columns(result, {"stance": "品牌态度"})

    def test_model_cannot_mutate_caller_record_or_rules(self):
        def model(request):
            request["project"]["target_brand"] = "other"
            request["record"]["text"] = "changed"
            return self.final
        labeler = self.labeler(model)
        result = labeler.label(self.row)
        self.assertEqual(labeler.project["target_brand"], "星舟")
        self.assertEqual(result["input"]["text"], self.row["text"])

    def test_other_project_uses_same_entry_without_event_labels(self):
        project = copy.deepcopy(self.project)
        project["project_id"], project["target_brand"] = "another-project", "银湾"
        project["aliases"] = []
        project["dimensions"] = {"topic": copy.deepcopy(project["dimensions"]["purpose"])}
        project["dimensions"]["topic"]["labels"] = {
            "delivery": {"name": "配送体验", "definition": "描述配送经历。"},
            "unknown": {"name": "无法判断", "definition": "信息不足。"}}
        row = dict(self.row, text="银湾送货很快。")
        evidence = [{"source": "text", "quote": "送货很快"}]
        pred = dict(self.pred, labels={"topic": [{"value": "delivery", "evidence": evidence}]}, evidence=evidence)
        labeler = self.labeler(Mock(return_value={"type": "prediction", "prediction": pred}), project=project)
        result = labeler.label(row)
        self.assertEqual(result["status"], "candidate")
        self.assertEqual(labeler.export_columns(result, {"topic": "题材领域"})["题材领域"], "配送体验")


class LabelRelationTests(unittest.TestCase):
    def setUp(self):
        self.project = io.read_json(ROOT / "examples/content-project.json")
        child = copy.deepcopy(self.project["dimensions"]["purpose"])
        child["labels"] = {code: {"name": code, "definition": "仅用于结构约束测试。"}
                           for code in ("consultation", "experience", "information", "unknown")}
        self.project["dimensions"] = {"main": self.project["dimensions"]["purpose"], "detail": child}
        self.project["label_relations"] = [{"parent": "main", "child": "detail", "allowed": {
            "question": ["consultation", "unknown"], "feedback": ["experience", "unknown"],
            "sharing": ["information", "unknown"], "unknown": ["unknown"]}}]

    def test_valid_relation_and_prediction_consistency(self):
        io.project_config(self.project)
        row = shared.adapt_record({"id": "n1", "text": "测试原文"}, id_column="id", text_column="text")
        evidence = [{"source": "text", "quote": "测试原文"}]
        pred = {"id": "n1", "targets": ["product"], "aspects": [], "behaviors": [],
                "labels": {"main": [{"value": "feedback", "evidence": evidence}],
                           "detail": [{"value": "experience", "evidence": evidence}]},
                "evidence": evidence, "reason": "结构测试", "confidence": "high", "flags": [], "needs_review": False}
        self.assertEqual(io.check_prediction_v2(pred, row, self.project), [])
        pred["labels"]["detail"][0]["value"] = "consultation"
        with self.assertRaisesRegex(ValueError, "hierarchy conflict"):
            io.check_prediction_v2(pred, row, self.project)

    def test_incomplete_invalid_or_multi_relation_rejected(self):
        relation = self.project["label_relations"][0]
        for replacement in (dict(relation, parent="missing"), dict(relation, child="main"),
                            dict(relation, allowed={}), dict(relation, allowed=dict(relation["allowed"], feedback=["bad"]))):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                io.project_config(dict(self.project, label_relations=[replacement]))
        self.project["dimensions"]["detail"]["mode"] = "multi"
        with self.assertRaises(ValueError):
            io.project_config(self.project)


if __name__ == "__main__":
    unittest.main()
