"""Offline legacy-row integration demo. Fixed mock replies, not an accuracy test."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/ai-comment-labeler/scripts"))
from shared_labeler import SharedLabeler, adapt_record, io


def mock_model(request):
    row = request["record"]
    if not row.get("context", {}).get("post_text"):
        return {"type": "context_request", "sources": ["post_text"],
                "reason": "需要知道原笔记介绍的产品，才能确定这款指什么。"}
    evidence = [{"source": "text", "quote": "这款用了一个月，音质很好"},
                {"source": "post_text", "quote": "星舟耳机"}]
    return {"type": "prediction", "prediction": {
        "id": row["id"], "targets": ["product"],
        "aspects": [{"name": "quality", "stance": "positive"}],
        "labels": {"purpose": [{"value": "feedback", "evidence": evidence}],
                   "stance": [{"value": "positive", "evidence": evidence}]},
        "behaviors": [], "evidence": evidence,
        "reason": "演示固定返回：作者描述正面体验，原笔记明确所指产品。",
        "confidence": "high", "flags": [], "needs_review": False}}


def mock_context_lookup(bound):
    # A real adapter must verify authorization AND the actual comment-to-note relation.
    if bound == {"kind": "post_text", "record_id": "demo-c1", "source_ref": "demo-note-1"}:
        return dict(bound, status="ok", text="这篇笔记介绍星舟耳机。")
    return dict(bound, status="not_found")


def demo():
    project = io.read_json(ROOT / "examples/content-project.json")
    # Show metadata-based target resolution, not a guess from brand frequency.
    del project["target_brand"]
    project["aliases"] = []
    labeler = SharedLabeler(project, mock_model, model_id="offline-fixed-mock",
                           catalog=[{"target": "星舟", "aliases": ["星舟耳机"], "is_own": True}],
                           context_resolver=mock_context_lookup)
    legacy_row = {"评论ID": "demo-c1", "评论内容": "这款用了一个月，音质很好。", "笔记ID": "demo-note-1"}
    record = adapt_record(legacy_row, id_column="评论ID", text_column="评论内容")
    result = labeler.label(record, context_refs={"post_text": legacy_row["笔记ID"]})
    return {"mode": "离线固定返回演示，未调用真实模型或检索服务，不是准确率验证",
            "output": labeler.export_columns(result, {"purpose": "判断标签", "stance": "品牌态度"}),
            "model_calls": result["model_calls"], "context_trace": result["context_trace"],
            "scope_resolution": result["provenance"]["scope_resolution"]}


if __name__ == "__main__":
    print(json.dumps(demo(), ensure_ascii=False, indent=2))
