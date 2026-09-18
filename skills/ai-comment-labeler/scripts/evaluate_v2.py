#!/usr/bin/env python3
"""Offline v2 reference comparison; no classifier, model call or inferred gold."""

import argparse
from collections import Counter
import importlib.util
from pathlib import Path
import sys

_spec = importlib.util.spec_from_file_location("eval_label_io", Path(__file__).with_name("label_io.py"))
io = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(io)
BEHAVIOR_KEYS = ("action", "actor", "stage", "time_scope")


def behavior_tuple(behavior, config, reference=False):
    if reference:
        io.keys(behavior, set(BEHAVIOR_KEYS) | ({"event_relation"} if "event" in config else set()))
    io.choices(behavior["action"], config["actions"], "reference action")
    io.choices(behavior["actor"], io.ACTORS, "reference actor")
    io.choices(behavior["stage"], io.STAGES, "reference stage")
    io.choices(behavior["time_scope"], io.TIME_SCOPES, "reference time scope")
    value = tuple(behavior[k] for k in BEHAVIOR_KEYS)
    if "event" in config:
        relation = behavior["event_relation"] if reference else behavior["event_link"]["relation"]
        io.choices(relation, io.EVENT_RELATIONS, "reference event relation")
        value += (relation,)
    return value


def set_metrics(actual, predicted, universe):
    """None prediction is missing/error, not an empty multi-label set."""
    total = len(actual)
    matched = sum(p is not None and a == p for a, p in zip(actual, predicted))
    support, guessed, hits = Counter(), Counter(), Counter()
    for a, p in zip(actual, predicted):
        support.update(a)
        guessed.update(p or set())
        hits.update(a & (p or set()))
    scores, f1s = {}, []
    for label in sorted(universe):
        count, n_pred, tp = support[label], guessed[label], hits[label]
        f1 = 2 * tp / (count + n_pred) if count + n_pred else None
        scores[label] = {"support": count, "predicted": n_pred,
                         "precision": tp / n_pred if n_pred else None,
                         "recall": tp / count if count else None, "f1": f1}
        if f1 is not None:
            f1s.append(f1)
    n_actual, n_pred, tp = sum(support.values()), sum(guessed.values()), sum(hits.values())
    return {"total": total, "exact_match_accuracy": matched / total if total else None,
            "prediction_coverage": sum(p is not None for p in predicted) / total if total else None,
            "micro_precision": tp / n_pred if n_pred else None,
            "micro_recall": tp / n_actual if n_actual else None,
            "micro_f1": 2 * tp / (n_actual + n_pred) if n_actual + n_pred else None,
            "macro_f1_present_labels": sum(f1s) / len(f1s) if f1s else None, "per_label": scores}


def evaluate(project, source_rows, reference, labeled_rows):
    config = io.project_config(project)
    io.require(io.is_v2(config), "v2 project required")
    io.require(source_rows, "empty evaluation input")
    io.inputs(source_rows, config)
    original = io.index_rows(source_rows)
    io.keys(reference, {"reference_kind", "reference_note", "project_sha256", "input_sha256", "rows"})
    io.choices(reference["reference_kind"], {"human", "development"}, "reference kind")
    io.text(reference["reference_note"], "reference provenance note", 4000)
    project_hash = io.digest(io.encode(config))
    io.require(reference["project_sha256"] == project_hash, "reference/project mismatch")
    io.require(reference["input_sha256"] == io.digest(io.encode(source_rows)), "reference/input mismatch")
    io.require(isinstance(reference["rows"], list), "reference rows must be array")
    gold = io.index_rows(reference["rows"])
    io.require(set(gold) == set(original), "reference must cover every input ID")
    for row in gold.values():
        io.keys(row, {"id", "labels", "behaviors"})
        io.keys(row["labels"], set(config["dimensions"]))
        for dim, spec in config["dimensions"].items():
            values = row["labels"][dim]
            io.string_list(values, "reference labels", spec["labels"])
            io.require(spec["mode"] != "single" or len(values) == 1, "single reference needs one label")
            io.require(spec["unknown"] not in values or len(values) == 1, "mixed unknown reference")
        for relation in config.get("label_relations", []):
            parent, child = (row["labels"][relation[k]][0] for k in ("parent", "child"))
            io.require(child in relation["allowed"][parent], "reference hierarchy conflict")
        io.require(isinstance(row["behaviors"], list), "reference behaviors must be array")
        tuples = [behavior_tuple(b, config, reference=True) for b in row["behaviors"]]
        io.require(len(tuples) == len(set(tuples)), "duplicate reference behavior")
        if "event" in config:
            for b in row["behaviors"]:
                if b["event_relation"] == "caused_by":
                    relevant = row["labels"][config["event"]["relevance_dimension"]][0]
                    io.require(relevant in config["event"]["related_labels"] and b["time_scope"] != "before_event",
                               "reference event cause conflict")
    labeled = io.index_rows(labeled_rows)
    io.require(set(labeled) <= set(original), "result contains unknown IDs")
    valid, automatic, errors, misses, models = {}, set(), [], [], set()
    for item_id, item in labeled.items():
        # Input/rule mismatch invalidates the comparison, rather than scoring a different task.
        io.require(item.get("input") == original[item_id], "result/input mismatch: " + item_id)
        provenance = item.get("provenance")
        io.require(isinstance(provenance, dict), "result missing provenance: " + item_id)
        io.require(provenance.get("project_sha256") == project_hash,
                   "result/project mismatch: " + item_id)
        model = provenance.get("model", "unspecified")
        io.text(model, "result model id", 200)
        models.add(model)
        if "status" in item:
            io.choices(item["status"], {"candidate", "review", "error"}, "result status")
            claimed_auto = item["status"] == "candidate"
        else:
            io.require(type(item.get("review_required")) is bool, "result missing review status")
            claimed_auto = not item["review_required"]
        prediction = item.get("prediction")
        if prediction is None or item.get("status") == "error":
            errors.append(item_id)
            continue
        try:
            reasons = io.check_prediction_v2(prediction, original[item_id], config)
        except (ValueError, TypeError, KeyError):
            errors.append(item_id)
            continue
        valid[item_id] = prediction
        # Recompute mandatory review; a tampered false/high cannot release known unknowns.
        if claimed_auto and not reasons and not item.get("review_details"):
            automatic.add(item_id)
    ids = list(original)
    dimensions = {}
    for dim, spec in config["dimensions"].items():
        actual = [set(gold[i]["labels"][dim]) for i in ids]
        predicted = [set(v["value"] for v in valid[i]["labels"][dim]) if i in valid else None for i in ids]
        score = set_metrics(actual, predicted, spec["labels"])
        score["mode"] = spec["mode"]
        selected = [n for n, i in enumerate(ids) if i in automatic]
        score["auto_candidate_exact_match_accuracy"] = (
            sum(actual[n] == predicted[n] for n in selected) / len(selected) if selected else None)
        if spec["mode"] == "single":
            matrix = {label: dict.fromkeys(spec["labels"], 0) for label in spec["labels"]}
            missing_by_label = dict.fromkeys(spec["labels"], 0)
            for a, p in zip(actual, predicted):
                if p is None:
                    missing_by_label[next(iter(a))] += 1
                else:
                    matrix[next(iter(a))][next(iter(p))] += 1
            score.update(confusion_matrix=matrix, missing_or_error_by_reference_label=missing_by_label)
        for i, a, p in zip(ids, actual, predicted):
            if p is None or a != p:
                misses.append({"id": i, "field": "labels." + dim,
                               "expected": sorted(a), "predicted": sorted(p) if p is not None else None})
        dimensions[dim] = score
    actual_b = [set(behavior_tuple(b, config, True) for b in gold[i]["behaviors"]) for i in ids]
    pred_b = [set(behavior_tuple(b, config) for b in valid[i]["behaviors"]) if i in valid else None for i in ids]
    # JSON-encoded tuples keep direction, actor, stage, time and cause observable.
    actual_s = [{io.encode(b) for b in values} for values in actual_b]
    pred_s = [None if values is None else {io.encode(b) for b in values} for values in pred_b]
    universe = set().union(*actual_s, *(p for p in pred_s if p is not None))
    behavior_scores = set_metrics(actual_s, pred_s, universe)
    behavior_scores["tuple_fields"] = list(BEHAVIOR_KEYS) + (["event_relation"] if "event" in config else [])
    for i, a, p in zip(ids, actual_b, pred_b):
        if p is None or a != p:
            misses.append({"id": i, "field": "behaviors", "expected": sorted(a),
                           "predicted": sorted(p) if p is not None else None})
    return {"total": len(ids), "unit": "input_record", "project_unit": config["unit"],
            "reference_kind": reference["reference_kind"], "reference_note": reference["reference_note"],
            "scope": "comparison_against_declared_reference_not_verified_business_accuracy",
            "project_sha256": project_hash, "input_sha256": reference["input_sha256"],
            "reference_sha256": io.digest(io.encode(reference)),
            "result_sha256": io.digest(io.encode(labeled_rows)), "model_ids": sorted(models),
            "rule_version": config["rule_version"],
            "missing_ids": sorted(set(original) - set(labeled)), "error_ids": sorted(errors),
            "prediction_coverage": len(valid) / len(ids), "auto_candidate_coverage": len(automatic) / len(ids),
            "all_dimensions_exact_match_accuracy": sum(i in valid and all(
                {v["value"] for v in valid[i]["labels"][dim]} == set(gold[i]["labels"][dim])
                for dim in config["dimensions"]) for i in ids) / len(ids),
            "dimensions": dimensions, "behaviors": behavior_scores, "mismatches": misses}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("project", "input", "gold", "labels"):
        parser.add_argument("--" + flag, required=True)
    args = parser.parse_args()
    try:
        result = evaluate(io.read_json(args.project), io.read_jsonl(args.input), io.read_json(args.gold),
                          io.read_jsonl(args.labels, allow_empty=True, max_chars=30000000, max_line=150000))
        print(io.encode(result))
    except (ValueError, OSError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
