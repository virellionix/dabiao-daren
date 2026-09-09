#!/usr/bin/env python3
"""Offline IO/checks for AI comment labeling; this is not a classifier."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys


STANCES = ("positive", "negative", "neutral", "mixed", "no_attitude", "uncertain")
TARGETS = {"brand", "product", "creator", "advertisement", "merchant",
           "platform", "competitor", "other", "none"}
FLAGS = {"sarcasm", "comparison", "quoted_opinion", "missing_context", "mixed"}
CONTEXT = {"title", "post_summary", "parent_comment"}
PREDICTION_KEYS = {"id", "stance", "targets", "aspects", "evidence", "reason",
                   "confidence", "flags", "needs_review"}
SKILL_ROOT = Path(__file__).resolve().parents[1]
MAX_ROWS, MAX_CHARS, MAX_LINE = 10000, 5000000, 50000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def parse(text):
    def invalid_constant(_):
        raise ValueError("non-finite JSON number")
    return json.loads(text, object_pairs_hook=strict_object,
                      parse_constant=invalid_constant)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path):
    with Path(path).open(encoding="utf-8") as file:
        raw = file.read(MAX_CHARS + 1)
    require(len(raw) <= MAX_CHARS, "JSON file exceeds character limit")
    return parse(raw)


def read_jsonl(path, allow_empty=False, max_chars=MAX_CHARS, max_line=MAX_LINE):
    rows, size = [], 0
    with Path(path).open(encoding="utf-8") as file:
        while True:
            line = file.readline(max_line + 1)
            if not line:
                break
            size += len(line)
            require(len(line) <= max_line and size <= max_chars,
                    "JSONL exceeds line/total character limit")
            if not line.strip():
                continue
            rows.append(parse(line))
            require(len(rows) <= MAX_ROWS, "JSONL exceeds row limit")
    require(rows or allow_empty, "empty JSONL dataset")
    return rows


def write_json(path, value):
    Path(path).write_text(encode(value) + "\n", encoding="utf-8")


def write_jsonl(path, rows):
    Path(path).write_text("".join(encode(row) + "\n" for row in rows), encoding="utf-8")


def text(value, name, limit=12000, empty=False):
    require(isinstance(value, str) and len(value) <= limit and
            (empty or bool(value.strip())), "invalid string: " + name)


def keys(value, required, optional=()):
    require(isinstance(value, dict), "expected JSON object")
    require(set(value) >= set(required) and set(value) <= set(required) | set(optional),
            "missing or unknown fields; expected: " + ", ".join(sorted(required)))


def choices(value, allowed, name):
    require(isinstance(value, str) and value in allowed, "invalid " + name)


def string_list(value, name, allowed=None, nonempty=False):
    require(isinstance(value, list) and len(value) <= 100 and
            (not nonempty or bool(value)), "invalid list: " + name)
    for item in value:
        text(item, name, 200)
        if allowed is not None:
            choices(item, allowed, name)
    require(len(value) == len(set(value)), "duplicate item: " + name)


def project_config(config):
    keys(config, {"project_id", "rule_version", "target_brand", "aliases", "aspects"})
    for name in ("project_id", "rule_version", "target_brand"):
        text(config[name], name, 200)
    string_list(config["aliases"], "aliases")
    string_list(config["aspects"], "aspects", nonempty=True)
    return config


def index_rows(rows):
    result = {}
    for row in rows:
        require(isinstance(row, dict) and "id" in row, "row lacks id")
        text(row["id"], "id", 200)
        require(row["id"] not in result, "duplicate row id: " + row["id"])
        result[row["id"]] = row
    return result


def inputs(rows):
    index_rows(rows)
    for row in rows:
        keys(row, {"id", "text"}, {"context"})
        text(row["text"], "text")
        if "context" in row:
            keys(row["context"], set(), CONTEXT)
            for key, value in row["context"].items():
                text(value, key, empty=True)
    return rows


def batches(rows, config, batch_size, max_chars):
    require(1 <= batch_size <= 100, "batch-size must be 1..100")
    require(1000 <= max_chars <= MAX_CHARS, "max-chars must be 1000..5000000")
    result, current = [], []
    for row in rows:
        payload = {"project": config, "comments": current + [row]}
        if current and (len(current) >= batch_size or len(encode(payload)) > max_chars):
            result.append({"project": config, "comments": current})
            current = []
        require(len(encode({"project": config, "comments": [row]})) <= max_chars,
                "single comment exceeds batch budget: " + row["id"])
        current.append(row)
    if current:
        result.append({"project": config, "comments": current})
    return result


def prepare(input_path, project_path, out, batch_size=20, max_chars=16000):
    rows = inputs(read_jsonl(input_path))
    config = project_config(read_json(project_path))
    chunks = batches(rows, config, batch_size, max_chars)
    snapshots = {"SKILL.md": (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")}
    for name in ("codebook.md", "contracts.md"):
        snapshots[name] = (SKILL_ROOT / "references" / name).read_text(encoding="utf-8")
    manifest = {
        "schema_version": 1, "input_sha256": digest(encode(rows)),
        "project_sha256": digest(encode(config)),
        "skill_sha256": digest(encode(snapshots)),
        "helper_sha256": digest(Path(__file__).read_text(encoding="utf-8")),
        "total": len(rows), "batches": len(chunks),
        "batch_sha256": [digest(encode(chunk)) for chunk in chunks],
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "batches").mkdir()
    (out / "predictions").mkdir()
    write_jsonl(out / "input.jsonl", rows)
    write_json(out / "project.json", config)
    write_json(out / "skill-snapshot.json", snapshots)
    for number, chunk in enumerate(chunks, 1):
        write_json(out / "batches" / ("%04d.json" % number), chunk)
    write_json(out / "manifest.json", manifest)  # completion marker, written last
    return {"prepared": len(rows), "batches": len(chunks), "labeled": 0}


def load_run(run):
    run = Path(run)
    manifest = read_json(run / "manifest.json")
    require(manifest.get("schema_version") == 1, "unsupported run schema")
    rows = inputs(read_jsonl(run / "input.jsonl"))
    config = project_config(read_json(run / "project.json"))
    snapshots = read_json(run / "skill-snapshot.json")
    checks = {"input_sha256": digest(encode(rows)),
              "project_sha256": digest(encode(config)),
              "skill_sha256": digest(encode(snapshots)),
              "helper_sha256": digest(Path(__file__).read_text(encoding="utf-8"))}
    require(all(manifest.get(k) == v for k, v in checks.items()),
            "run input/project/skill/helper changed; prepare a new run")
    chunks = [read_json(path) for path in sorted((run / "batches").glob("*.json"))]
    require([digest(encode(chunk)) for chunk in chunks] == manifest["batch_sha256"],
            "run batches changed")
    require(manifest["total"] == len(rows) and manifest["batches"] == len(chunks),
            "invalid manifest counts")
    return rows, config, manifest


def check_prediction(prediction, row, config):
    keys(prediction, PREDICTION_KEYS)
    require(prediction["id"] == row["id"], "mismatched prediction id")
    choices(prediction["stance"], STANCES, "stance")
    choices(prediction["confidence"], {"high", "medium", "low"}, "confidence")
    string_list(prediction["targets"], "targets", TARGETS, nonempty=True)
    require("none" not in prediction["targets"] or len(prediction["targets"]) == 1,
            "none cannot coexist with other targets")
    string_list(prediction["flags"], "flags", FLAGS)
    require(type(prediction["needs_review"]) is bool, "needs_review must be boolean")
    text(prediction["reason"], "reason", 400)
    require(isinstance(prediction["aspects"], list), "aspects must be array")
    aspect_pairs = []
    for aspect in prediction["aspects"]:
        keys(aspect, {"name", "stance"})
        choices(aspect["name"], config["aspects"], "aspect name")
        choices(aspect["stance"], {"positive", "negative", "neutral"}, "aspect stance")
        aspect_pairs.append((aspect["name"], aspect["stance"]))
    require(len(aspect_pairs) == len(set(aspect_pairs)), "duplicate aspect opinion")
    if prediction["stance"] == "no_attitude":
        require(not aspect_pairs, "no_attitude cannot have target-brand aspect opinions")
    if prediction["stance"] in {"positive", "negative", "neutral", "mixed"}:
        require(set(prediction["targets"]) & {"brand", "product", "advertisement", "merchant"},
                "brand stance lacks in-scope target type")
    evidence = prediction["evidence"]
    require(isinstance(evidence, list) and evidence, "evidence must be nonempty array")
    current_comment_evidence = False
    for item in evidence:
        keys(item, {"source", "quote"})
        choices(item["source"], CONTEXT | {"text"}, "evidence source")
        text(item["quote"], "evidence quote")
        source = row["text"] if item["source"] == "text" else row.get("context", {}).get(item["source"], "")
        require(item["quote"] in source, "evidence is not an original substring: " + row["id"])
        current_comment_evidence |= item["source"] == "text"
    require(current_comment_evidence, "need evidence from current comment, not only context")
    reasons = []
    if prediction["stance"] in {"uncertain", "mixed"}:
        reasons.append("stance_" + prediction["stance"])
    if prediction["confidence"] != "high":
        reasons.append("confidence_" + prediction["confidence"])
    if prediction["needs_review"]:
        reasons.append("model_requested")
    reasons.extend("flag_" + flag for flag in sorted(prediction["flags"]))
    aspect_stances = {pair[1] for pair in aspect_pairs}
    overall = prediction["stance"]
    if (overall == "positive" and "negative" in aspect_stances or
            overall == "negative" and "positive" in aspect_stances or
            overall == "neutral" and aspect_stances & {"positive", "negative"}):
        reasons.append("stance_aspect_conflict")
    return reasons


def prediction_rows(path):
    path = Path(path)
    if path.is_file():
        return read_jsonl(path)
    require(path.is_dir(), "predictions path does not exist")
    rows = []
    total_chars = 0
    for file in sorted(path.glob("*.jsonl")):
        part = read_jsonl(file)
        total_chars += sum(len(encode(row)) + 1 for row in part)
        rows.extend(part)
        require(len(rows) <= MAX_ROWS and total_chars <= MAX_CHARS,
                "combined predictions exceed limits")
    require(rows, "no prediction JSONL files")
    return rows


def validate(run, predictions_path, out, model):
    text(model, "model", 200)
    rows, config, manifest = load_run(run)
    predictions = index_rows(prediction_rows(predictions_path))
    original = index_rows(rows)
    missing, extra = set(original) - set(predictions), set(predictions) - set(original)
    require(not missing and not extra,
            "prediction coverage mismatch: missing=%d extra=%d" % (len(missing), len(extra)))
    provenance = {key: manifest[key] for key in
                  ("input_sha256", "project_sha256", "skill_sha256", "helper_sha256")}
    provenance.update({"model": model, "rule_version": config["rule_version"],
                       "checked_at": datetime.now(timezone.utc).isoformat(),
                       "confidence_calibration": "uncalibrated"})
    labeled = []
    for row in rows:
        prediction = predictions[row["id"]]
        reasons = check_prediction(prediction, row, config)
        labeled.append({"id": row["id"], "input": row, "prediction": prediction,
                        "review_required": bool(reasons), "review_reasons": reasons,
                        "provenance": provenance})
    review = [row for row in labeled if row["review_required"]]
    summary = {"total": len(rows), "review_required": len(review),
               "auto_candidates": len(rows) - len(review),
               "auto_candidate_coverage": (len(rows) - len(review)) / len(rows),
               "stance_counts": dict(Counter(row["prediction"]["stance"] for row in labeled)),
               "semantic_accuracy": "not_evaluated", "provenance": provenance}
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    write_jsonl(out / "labels.jsonl", labeled)
    write_jsonl(out / "review.jsonl", review)
    write_json(out / "summary.json", summary)
    return summary


def evaluate(gold_path, labels_path):
    gold = index_rows(read_jsonl(gold_path))
    # Checked rows contain input + prediction + provenance; do not impose the
    # smaller raw-input budget on this expanded but still bounded format.
    labeled = index_rows(read_jsonl(labels_path, max_chars=30000000, max_line=150000))
    require(set(gold) == set(labeled), "gold/result IDs must match exactly")
    for row in gold.values():
        keys(row, {"id", "stance"})
        choices(row["stance"], STANCES, "gold stance")
    for row in labeled.values():
        require(isinstance(row.get("prediction"), dict), "invalid checked result")
        require(row["prediction"].get("id") == row["id"], "checked prediction id mismatch")
        choices(row["prediction"].get("stance"), STANCES, "predicted stance")
        require(type(row.get("review_required")) is bool, "missing review status")
    matrix = {actual: {predicted: 0 for predicted in STANCES} for actual in STANCES}
    for key in gold:
        matrix[gold[key]["stance"]][labeled[key]["prediction"]["stance"]] += 1
    scores, f1s = {}, []
    for label in STANCES:
        tp = matrix[label][label]
        support = sum(matrix[label].values())
        predicted = sum(matrix[actual][label] for actual in STANCES)
        f1 = 2 * tp / (support + predicted) if support + predicted else None
        scores[label] = {"support": support, "predicted": predicted,
                         "precision": tp / predicted if predicted else None,
                         "recall": tp / support if support else None, "f1": f1}
        if f1 is not None:
            f1s.append(f1)
    correct = sum(matrix[label][label] for label in STANCES)
    automatic = [key for key in gold if not labeled[key]["review_required"]]
    auto_correct = sum(gold[key]["stance"] == labeled[key]["prediction"]["stance"] for key in automatic)
    return {"total": len(gold), "accuracy": correct / len(gold),
            "macro_f1_present_labels": sum(f1s) / len(f1s), "per_label": scores,
            "confusion_matrix": matrix, "auto_candidate_coverage": len(automatic) / len(gold),
            "auto_candidate_accuracy": auto_correct / len(automatic) if automatic else None,
            "scope": "stance_only_against_supplied_reference_not_a_business_guarantee"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    for flag in ("input", "project", "out"):
        prep.add_argument("--" + flag, required=True)
    prep.add_argument("--batch-size", type=int, default=20)
    prep.add_argument("--max-chars", type=int, default=16000)
    check = commands.add_parser("validate")
    for flag in ("run", "predictions", "out", "model"):
        check.add_argument("--" + flag, required=True)
    score = commands.add_parser("evaluate")
    score.add_argument("--gold", required=True)
    score.add_argument("--labels", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.input, args.project, args.out, args.batch_size, args.max_chars)
        elif args.command == "validate":
            result = validate(args.run, args.predictions, args.out, args.model)
        else:
            result = evaluate(args.gold, args.labels)
        print(encode(result))
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
