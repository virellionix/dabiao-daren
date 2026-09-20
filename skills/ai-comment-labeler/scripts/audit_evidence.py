#!/usr/bin/env python3
"""Audit evidence traceability without calling a model.

This command measures whether a result is structurally tied to its frozen
input. It cannot decide whether a quote *semantically* proves a label; that
still needs an independent human review set.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

try:
    import label_io as io
except ImportError:  # pragma: no cover - direct module loading
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import label_io as io


def read_jsonl(path):
    return io.read_jsonl(path)


def _source_text(row, source):
    return row["text"] if source == "text" else row.get("context", {}).get(source, "")


def _evidence_items(prediction):
    if not isinstance(prediction, dict):
        return []
    items = [("evidence", item) for item in prediction.get("evidence", [])]
    for dimension, values in prediction.get("labels", {}).items():
        for index, value in enumerate(values if isinstance(values, list) else []):
            for item in value.get("evidence", []) if isinstance(value, dict) else []:
                items.append((f"labels.{dimension}[{index}].evidence", item))
    for index, behavior in enumerate(prediction.get("behaviors", [])):
        for item in behavior.get("evidence", []) if isinstance(behavior, dict) else []:
            items.append((f"behaviors[{index}].evidence", item))
        if isinstance(behavior, dict):
            for item in behavior.get("event_link", {}).get("evidence", []):
                items.append((f"behaviors[{index}].event_link.evidence", item))
    return items


def audit(project, rows, results):
    project = io.project_config(project)
    io.inputs(rows, project)
    original = io.index_rows(rows)
    indexed = {}
    duplicate_ids = []
    for result in results:
        item_id = result.get("id") if isinstance(result, dict) else None
        if item_id in indexed:
            duplicate_ids.append(item_id)
        indexed[item_id] = result
    missing_ids = sorted(set(original) - set(indexed))
    extra_ids = sorted(set(indexed) - set(original))
    counts = Counter()
    issues = []
    for item_id in sorted(set(original) & set(indexed)):
        row, result = original[item_id], indexed[item_id]
        prediction = result.get("prediction") if isinstance(result, dict) else None
        if prediction is None:
            counts["error_results"] += 1
            issues.append({"id": item_id, "field": "prediction", "reason": "missing_prediction"})
            continue
        try:
            (io.check_prediction_v2 if io.is_v2(project) else io.check_prediction)(prediction, row, project)
            counts["structurally_valid"] += 1
        except (ValueError, TypeError, KeyError) as exc:
            counts["structurally_invalid"] += 1
            issues.append({"id": item_id, "field": "prediction", "reason": "schema_error",
                           "detail": str(exc)})
        evidence = _evidence_items(prediction)
        counts["records_with_prediction"] += 1
        counts["evidence_items"] += len(evidence)
        if not evidence:
            counts["records_without_evidence"] += 1
            issues.append({"id": item_id, "field": "evidence", "reason": "no_evidence"})
        for field, item in evidence:
            if not isinstance(item, dict) or not isinstance(item.get("source"), str) or not isinstance(item.get("quote"), str):
                counts["malformed_evidence"] += 1
                issues.append({"id": item_id, "field": field, "reason": "malformed_evidence"})
                continue
            source, quote = item["source"], item["quote"]
            source_text = _source_text(row, source)
            if not source_text:
                counts["unavailable_source"] += 1
                issues.append({"id": item_id, "field": field, "reason": "source_unavailable", "source": source})
            elif quote not in source_text:
                counts["quote_mismatch"] += 1
                issues.append({"id": item_id, "field": field, "reason": "quote_not_substring", "source": source})
            else:
                counts["evidence_bound"] += 1
                if source != "text":
                    counts["context_evidence"] += 1
    if missing_ids:
        issues.append({"field": "results", "reason": "missing_ids", "count": len(missing_ids)})
    if extra_ids:
        issues.append({"field": "results", "reason": "extra_ids", "count": len(extra_ids)})
    if duplicate_ids:
        issues.append({"field": "results", "reason": "duplicate_ids", "ids": duplicate_ids})
    total_evidence = counts["evidence_items"]
    return {
        "semantic_accuracy": "not_measured",
        "records": len(rows),
        "results": len(results),
        "missing_ids": missing_ids,
        "extra_ids": extra_ids,
        "duplicate_ids": duplicate_ids,
        "counts": dict(counts),
        "evidence_binding_rate": (counts["evidence_bound"] / total_evidence
                                   if total_evidence else None),
        "issues": issues,
        "pass": not issues,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--out")
    parser.add_argument("--fail-on-findings", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = audit(io.read_json(args.project), io.read_jsonl(args.input), read_jsonl(args.results))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 2 if args.fail_on_findings and not report["pass"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
