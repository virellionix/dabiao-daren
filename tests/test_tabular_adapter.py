import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/ai-comment-labeler/scripts/tabular_adapter.py"
spec = importlib.util.spec_from_file_location("tabular_adapter", SCRIPT)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class TabularAdapterTests(unittest.TestCase):
    def test_csv_prepare_preserves_context_and_does_not_invent_id(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.csv"
            source.write_text("评论ID,评论内容,笔记标题\nc1,这款不错,星舟耳机\n", encoding="utf-8")
            headers, rows = adapter.read_rows(source)
            result = adapter.prepare_rows(rows, id_column="评论ID", text_column="评论内容",
                                          context_columns={"title": "笔记标题"})
        self.assertEqual(headers, ["评论ID", "评论内容", "笔记标题"])
        self.assertEqual(result[0]["id"], "c1")
        self.assertEqual(result[0]["context"], {"title": "星舟耳机"})

    def test_merge_joins_by_id_and_keeps_input_order(self):
        original = [{"评论ID": "c1", "评论内容": "a"}, {"评论ID": "c2", "评论内容": "b"}]
        results = [
            {"id": "c2", "status": "candidate", "prediction": {"stance": "negative", "evidence": [], "behaviors": []}},
            {"id": "c1", "status": "review", "review_details": [{"detail": "需复核"}], "prediction": None},
        ]
        headers, rows = adapter.merge_rows(original, results, id_column="评论ID")
        self.assertEqual([row["评论ID"] for row in rows], ["c1", "c2"])
        self.assertEqual(rows[0]["打标状态"], "review")
        self.assertEqual(json.loads(rows[1]["AI标签JSON"]), {"stance": "negative"})
        self.assertIn("打标状态", headers)

    def test_duplicate_headers_rejected(self):
        with self.assertRaises(ValueError):
            adapter._validate_headers(["id", "id"])


if __name__ == "__main__":
    unittest.main()
