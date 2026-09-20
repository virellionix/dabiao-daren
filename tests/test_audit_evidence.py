import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "audit_evidence", ROOT / "skills/ai-comment-labeler/scripts/audit_evidence.py")
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


class EvidenceAuditTests(unittest.TestCase):
    def setUp(self):
        self.project = json.loads((ROOT / "examples/content-project.json").read_text(encoding="utf-8"))
        self.row = {"id": "c1", "document_id": "c1", "segment_index": 1,
                    "is_last_segment": True, "source_kind": "full_text",
                    "text": "这款用了一个月，音质很好。", "context": {"post_text": "星舟耳机"}}
        evidence = [{"source": "text", "quote": "音质很好"}]
        self.prediction = {"id": "c1", "targets": ["product"], "aspects": [],
                           "labels": {"purpose": [{"value": "feedback", "evidence": evidence}],
                                      "stance": [{"value": "positive", "evidence": evidence}]},
                           "behaviors": [], "evidence": evidence, "reason": "证据测试。",
                           "confidence": "high", "flags": [], "needs_review": False}

    def test_bound_evidence_passes(self):
        report = audit_module.audit(self.project, [self.row],
                                    [{"id": "c1", "prediction": self.prediction}])
        self.assertTrue(report["pass"])
        self.assertEqual(report["counts"].get("quote_mismatch", 0), 0)

    def test_quote_mismatch_is_reported(self):
        prediction = json.loads(json.dumps(self.prediction))
        prediction["evidence"][0]["quote"] = "不存在"
        report = audit_module.audit(self.project, [self.row],
                                    [{"id": "c1", "prediction": prediction}])
        self.assertFalse(report["pass"])
        self.assertGreater(report["counts"]["quote_mismatch"], 0)


if __name__ == "__main__":
    unittest.main()
