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
CONTEXT = {"title", "post_summary", "post_text", "parent_comment", "author_profile",
           "image_ocr", "image_description"}
PREDICTION_KEYS = {"id", "stance", "targets", "aspects", "evidence", "reason",
                   "confidence", "flags", "needs_review"}
SKILL_ROOT = Path(__file__).resolve().parents[1]
MAX_ROWS, MAX_CHARS, MAX_LINE = 10000, 5000000, 50000
STAGES = ("done", "ongoing", "planned", "considering", "suggested", "hypothetical", "unknown")
ACTORS = {"self", "other", "unspecified"}
TIME_SCOPES = {"before_event", "after_event", "unspecified"}
EVENT_RELATIONS = {"caused_by", "not_caused_by", "unknown"}


def is_v2(config):
    return config.get("schema_version") == 2


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
    require(isinstance(config, dict), "project must be object")
    base = {"project_id", "rule_version", "target_brand", "aliases", "aspects"}
    if "schema_version" in config:
        require(type(config["schema_version"]) is int and config["schema_version"] == 2,
                "unsupported project schema_version")
        keys(config, base | {"schema_version", "unit", "dimensions", "actions", "denominator", "rules"},
             {"require_ai_review", "label_relations", "event"})
        if "require_ai_review" in config:
            require(type(config["require_ai_review"]) is bool, "require_ai_review must be boolean")
    else:
        keys(config, base)
    for name in ("project_id", "rule_version", "target_brand"):
        text(config[name], name, 200)
    string_list(config["aliases"], "aliases")
    string_list(config["aspects"], "aspects", nonempty=True)
    if is_v2(config):
        choices(config["unit"], {"item", "segment"}, "unit")
        dimensions = config["dimensions"]
        require(isinstance(dimensions, dict) and 1 <= len(dimensions) <= 24,
                "dimensions must contain 1..24 entries")
        for name, spec in dimensions.items():
            text(name, "dimension key", 100)
            keys(spec, {"title", "mode", "labels", "unknown", "aggregate", "review_labels"})
            text(spec["title"], "dimension title", 200)
            choices(spec["mode"], {"single", "multi"}, "dimension mode")
            choices(spec["aggregate"], {"final", "union"}, "aggregate")
            catalog(spec["labels"])
            choices(spec["unknown"], spec["labels"], "unknown label")
            string_list(spec["review_labels"], "review_labels", spec["labels"])
        catalog(config["actions"], allow_empty=True)
        require(isinstance(config["rules"], list) and len(config["rules"]) <= 100,
                "rules must be an array of up to 100 strings")
        for rule in config["rules"]:
            text(rule, "rule", 4000)
        relations = config.get("label_relations", [])
        require(isinstance(relations, list) and len(relations) <= 24,
                "label_relations must be an array of up to 24 entries")
        for relation in relations:
            keys(relation, {"parent", "child", "allowed"})
            choices(relation["parent"], dimensions, "parent dimension")
            choices(relation["child"], dimensions, "child dimension")
            require(relation["parent"] != relation["child"], "relation dimensions must differ")
            parent, child = (dimensions[relation[k]] for k in ("parent", "child"))
            require(parent["mode"] == child["mode"] == "single",
                    "label relations require single-label dimensions")
            keys(relation["allowed"], set(parent["labels"]))
            for allowed in relation["allowed"].values():
                string_list(allowed, "allowed child labels", child["labels"], nonempty=True)
        if "event" in config:
            event = config["event"]
            keys(event, {"id", "description", "relevance_dimension", "related_labels"})
            text(event["id"], "event id", 200)
            text(event["description"], "event description", 4000)
            choices(event["relevance_dimension"], dimensions, "event relevance dimension")
            relevance = dimensions[event["relevance_dimension"]]
            require(relevance["mode"] == "single", "event relevance must be single-label")
            string_list(event["related_labels"], "related event labels", relevance["labels"], nonempty=True)
            require(relevance["unknown"] not in event["related_labels"], "unknown is not event relevance")
        denominator = config["denominator"]
        if denominator is not None:
            keys(denominator, {"dimension", "include"})
            choices(denominator["dimension"], dimensions, "denominator dimension")
            spec = dimensions[denominator["dimension"]]
            require(spec["mode"] == "single" and spec["aggregate"] == "final",
                    "denominator must use a final single-label dimension")
            string_list(denominator["include"], "denominator include", spec["labels"], nonempty=True)
    return config


def catalog(labels, allow_empty=False):
    require(isinstance(labels, dict) and (allow_empty or labels) and len(labels) <= 256,
            "label/action catalog must be object with at most 256 entries")
    for key, label in labels.items():
        text(key, "label key", 200)
        keys(label, {"name", "definition"}, {"validation_status"})
        text(label["name"], "label name", 200)
        text(label["definition"], "label definition", 2000)
        if "validation_status" in label:
            choices(label["validation_status"], {"unverified", "trial_passed"}, "validation_status")


def index_rows(rows):
    result = {}
    for row in rows:
        require(isinstance(row, dict) and "id" in row, "row lacks id")
        text(row["id"], "id", 200)
        require(row["id"] not in result, "duplicate row id: " + row["id"])
        result[row["id"]] = row
    return result


def inputs(rows, config=None):
    index_rows(rows)
    v2 = config is not None and is_v2(config)
    for row in rows:
        required = {"id", "text"}
        if v2:
            required |= {"document_id", "segment_index", "is_last_segment", "source_kind"}
        keys(row, required, {"context"})
        text(row["text"], "text")
        if "context" in row:
            keys(row["context"], set(), CONTEXT)
            for key, value in row["context"].items():
                text(value, key, empty=True)
        if v2:
            text(row["document_id"], "document_id", 200)
            require(type(row["segment_index"]) is int and row["segment_index"] > 0,
                    "segment_index must be positive integer")
            require(type(row["is_last_segment"]) is bool, "is_last_segment must be boolean")
            choices(row["source_kind"], {"full_text", "excerpt"}, "source_kind")
    if v2:
        groups = {}
        for row in rows:
            groups.setdefault(row["document_id"], []).append(row)
        for group in groups.values():
            ordered = sorted(group, key=lambda row: row["segment_index"])
            require([r["segment_index"] for r in ordered] == list(range(1, len(group) + 1)),
                    "segment indexes must be unique and contiguous from 1")
            require([r["is_last_segment"] for r in ordered] == [False] * (len(group) - 1) + [True],
                    "exactly the final segment must be marked last")
            require(config["unit"] != "item" or len(group) == 1,
                    "item mode requires one row per document")
    return rows


def batches(rows, config, batch_size, max_chars):
    require(1 <= batch_size <= 100, "batch-size must be 1..100")
    require(1000 <= max_chars <= MAX_CHARS, "max-chars must be 1000..5000000")
    result, current = [], []
    # Large rule packs are stored once. Character budgets apply to content;
    # the model must load the frozen project separately, as SKILL.md requires.
    project_payload = ({"project_id": config["project_id"], "rule_version": config["rule_version"],
                        "rules_file": "../project.json"} if is_v2(config) else config)
    for row in rows:
        payload = {"project": project_payload, "comments": current + [row]}
        if current and (len(current) >= batch_size or len(encode(payload)) > max_chars):
            result.append({"project": project_payload, "comments": current})
            current = []
        require(len(encode({"project": project_payload, "comments": [row]})) <= max_chars,
                "single comment exceeds batch budget: " + row["id"])
        current.append(row)
    if current:
        result.append({"project": project_payload, "comments": current})
    return result


def prepare(input_path, project_path, out, batch_size=20, max_chars=16000):
    config = project_config(read_json(project_path))
    rows = inputs(read_jsonl(input_path), config)
    chunks = batches(rows, config, batch_size, max_chars)
    snapshots = {"SKILL.md": (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")}
    for name in ("codebook.md", "contracts.md", "rule-packs.md", "labeling-policy.md"):
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
    config = project_config(read_json(run / "project.json"))
    rows = inputs(read_jsonl(run / "input.jsonl"), config)
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


def check_shared(prediction, row, config):
    require(prediction["id"] == row["id"], "mismatched prediction id")
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
    check_evidence(prediction["evidence"], row)
    reasons = []
    if prediction["confidence"] != "high":
        reasons.append("confidence_" + prediction["confidence"])
    if prediction["needs_review"]:
        reasons.append("model_requested")
    reasons.extend("flag_" + flag for flag in sorted(prediction["flags"]))
    return reasons, {pair[1] for pair in aspect_pairs}


def check_evidence(evidence, row, allow_empty=False):
    require(isinstance(evidence, list) and (allow_empty or evidence), "evidence must be nonempty array")
    if not evidence:
        return
    current_comment_evidence = False
    for item in evidence:
        keys(item, {"source", "quote"})
        choices(item["source"], CONTEXT | {"text"}, "evidence source")
        text(item["quote"], "evidence quote")
        source = row["text"] if item["source"] == "text" else row.get("context", {}).get(item["source"], "")
        require(item["quote"] in source, "evidence is not an original substring: " + row["id"])
        current_comment_evidence |= item["source"] == "text"
    require(current_comment_evidence, "need evidence from current comment, not only context")


def check_prediction(prediction, row, config):
    keys(prediction, PREDICTION_KEYS)
    choices(prediction["stance"], STANCES, "stance")
    reasons, aspect_stances = check_shared(prediction, row, config)
    if prediction["stance"] == "no_attitude":
        require(not prediction["aspects"], "no_attitude cannot have target-brand aspect opinions")
    if prediction["stance"] in {"positive", "negative", "neutral", "mixed"}:
        require(set(prediction["targets"]) & {"brand", "product", "advertisement", "merchant"},
                "brand stance lacks in-scope target type")
    if prediction["stance"] in {"uncertain", "mixed"}:
        reasons.append("stance_" + prediction["stance"])
    overall = prediction["stance"]
    if (overall == "positive" and "negative" in aspect_stances or
            overall == "negative" and "positive" in aspect_stances or
            overall == "neutral" and aspect_stances & {"positive", "negative"}):
        reasons.append("stance_aspect_conflict")
    return reasons


def check_prediction_v2(prediction, row, config):
    keys(prediction, (PREDICTION_KEYS - {"stance"}) | {"labels", "behaviors"}, {"ai_review"})
    reasons, _ = check_shared(prediction, row, config)
    keys(prediction["labels"], set(config["dimensions"]))
    for dimension, spec in config["dimensions"].items():
        values = prediction["labels"][dimension]
        require(isinstance(values, list), "dimension values must be array")
        require(spec["mode"] != "single" or len(values) == 1,
                "single dimension requires exactly one label: " + dimension)
        seen = set()
        for item in values:
            keys(item, {"value", "evidence"})
            choices(item["value"], spec["labels"], "dimension label: " + dimension)
            require(item["value"] not in seen, "duplicate dimension label")
            seen.add(item["value"])
            check_evidence(item["evidence"], row, allow_empty=item["value"] == spec["unknown"])
            if item["value"] in spec["review_labels"] or item["value"] == spec["unknown"]:
                reasons.append("label_" + dimension + ":" + item["value"])
            if spec["labels"][item["value"]].get("validation_status") == "unverified":
                reasons.append("unverified_label_" + dimension + ":" + item["value"])
        require(spec["unknown"] not in seen or len(seen) == 1,
                "unknown label cannot coexist with concrete labels")
    for relation in config.get("label_relations", []):
        parent = prediction["labels"][relation["parent"]][0]["value"]
        child = prediction["labels"][relation["child"]][0]["value"]
        require(child in relation["allowed"][parent], "label hierarchy conflict: " + relation["child"])
    require(isinstance(prediction["behaviors"], list), "behaviors must be array")
    seen_behaviors = set()
    for behavior in prediction["behaviors"]:
        required = {"action", "actor", "stage", "time_scope", "evidence"}
        keys(behavior, required | ({"event_link"} if "event" in config else set()))
        choices(behavior["action"], config["actions"], "behavior action")
        choices(behavior["actor"], ACTORS, "behavior actor")
        choices(behavior["stage"], STAGES, "behavior stage")
        choices(behavior["time_scope"], TIME_SCOPES, "behavior time_scope")
        check_evidence(behavior["evidence"], row)
        if "event" in config:
            event, link = config["event"], behavior["event_link"]
            keys(link, {"event_id", "relation", "evidence"})
            require(link["event_id"] == event["id"], "behavior linked to a different event")
            choices(link["relation"], EVENT_RELATIONS, "event relation")
            check_evidence(link["evidence"], row, allow_empty=link["relation"] == "unknown")
            if link["relation"] == "caused_by":
                relevance = prediction["labels"][event["relevance_dimension"]][0]["value"]
                require(relevance in event["related_labels"], "event cause requires event relevance")
                require(behavior["time_scope"] != "before_event", "event cannot cause a prior action")
            elif link["relation"] == "unknown":
                reasons.append("event_cause_unknown")
        key = tuple(behavior[k] for k in ("action", "actor", "stage", "time_scope"))
        require(key not in seen_behaviors, "duplicate behavior")
        seen_behaviors.add(key)
        if behavior["actor"] == "unspecified" or behavior["stage"] == "unknown":
            reasons.append("behavior_uncertain")
        if config["actions"][behavior["action"]].get("validation_status") == "unverified":
            reasons.append("unverified_action_" + behavior["action"])
    require(not config.get("require_ai_review") or "ai_review" in prediction,
            "project requires explicit ai_review before delivery")
    if "ai_review" in prediction:
        review = prediction["ai_review"]
        keys(review, {"checked_fields", "issues"})
        fields = review_fields(config)
        string_list(review["checked_fields"], "checked_fields", fields, nonempty=True)
        require(set(review["checked_fields"]) == fields, "ai_review must cover every prediction field")
        require(isinstance(review["issues"], list) and len(review["issues"]) <= 100,
                "ai_review issues must be array")
        issue_fields = fields | {"source", "record"} | {
            "behaviors[%d]" % i for i in range(len(prediction["behaviors"]))}
        for issue in review["issues"]:
            keys(issue, {"field", "detail"})
            choices(issue["field"], issue_fields, "review issue field")
            text(issue["detail"], "review issue detail", 400)
        if review["issues"]:
            reasons.append("ai_review_issue")
    if row["source_kind"] == "excerpt":
        reasons.append("excerpt_not_full_source")
    return list(dict.fromkeys(reasons))


def review_fields(config):
    return {"targets", "aspects", "behaviors"} | {"labels." + d for d in config["dimensions"]}


def review_details_v2(prediction, row, config, reasons):
    """Expose the exact review location, without turning candidates into truth."""
    details, covered = [], set()

    def add(field, reason, detail):
        details.append({"field": field, "reason": reason, "detail": detail})
        covered.add(reason)

    for dimension, spec in config["dimensions"].items():
        for item in prediction["labels"][dimension]:
            suffix = dimension + ":" + item["value"]
            for prefix, note in (("label_", "项目规则要求复核此标签"),
                                 ("unverified_label_", "此标签尚缺有效试标验证，不能自动放行")):
                reason = prefix + suffix
                if reason in reasons:
                    add("labels." + dimension, reason, spec["title"] + "：" +
                        spec["labels"][item["value"]]["name"] + "；" + note + "。")
    for i, behavior in enumerate(prediction["behaviors"]):
        field = "behaviors[%d]" % i
        if behavior["actor"] == "unspecified" or behavior["stage"] == "unknown":
            missing = []
            if behavior["actor"] == "unspecified":
                missing.append("是谁做的")
            if behavior["stage"] == "unknown":
                missing.append("动作进行到哪一步")
            add(field, "behavior_uncertain", "需明确" + "、".join(missing) + "；其他已确定行为保留。")
        reason = "unverified_action_" + behavior["action"]
        if reason in reasons:
            add(field, reason, "该动作尚未完成有效试标验证。")
        if behavior.get("event_link", {}).get("relation") == "unknown":
            add(field, "event_cause_unknown", "行为可保留，但尚无证据认定由本次事件引起。")
    for issue in prediction.get("ai_review", {}).get("issues", []):
        add(issue["field"], "ai_review_issue", issue["detail"])
    if prediction.get("ai_review", {}).get("issues"):
        covered.add("model_requested")  # the model supplied a more precise location
    if "excerpt_not_full_source" in reasons:
        add("source", "excerpt_not_full_source", "当前只有摘录；可保留摘录内的判断，但不能验收全文覆盖与最终状态。")
    for reason in reasons:
        if reason not in covered:
            add("record", reason, "需复核本条的置信度、语境或整体判断：" + reason)
    status = {field: ("review" if any(d["field"] in {"record", field} or
                                    field == "behaviors" and d["field"].startswith("behaviors[")
                                    for d in details) else "candidate")
              for field in sorted(review_fields(config))}
    return details, status


def rollup_v2(labeled, config):
    groups = {}
    for row in labeled:
        groups.setdefault(row["input"]["document_id"], []).append(row)
    documents = []
    for doc_id, group in groups.items():
        group.sort(key=lambda row: row["input"]["segment_index"])
        final, ever = {}, {}
        for dimension, spec in config["dimensions"].items():
            ever[dimension] = list(dict.fromkeys(item["value"] for row in group
                                  for item in row["prediction"]["labels"][dimension]))
            final[dimension] = ([item["value"] for item in group[-1]["prediction"]["labels"][dimension]]
                                if spec["aggregate"] == "final" else ever[dimension])
            # A union can include unknown at an earlier point. Preserve it in
            # ever_seen, but do not make it a simultaneous concrete label.
            if len(final[dimension]) > 1 and spec["unknown"] in final[dimension]:
                final[dimension] = [v for v in final[dimension] if v != spec["unknown"]]
        strongest = {}
        for row in group:
            for behavior in row["prediction"]["behaviors"]:
                key = tuple(behavior[k] for k in ("action", "actor", "time_scope"))
                if "event" in config:
                    key += (behavior["event_link"]["relation"],)
                if key not in strongest or STAGES.index(behavior["stage"]) < STAGES.index(strongest[key]["stage"]):
                    strongest[key] = {**behavior, "source_id": row["id"]}
        denominator = config["denominator"]
        included = denominator is None or bool(set(final[denominator["dimension"]]) & set(denominator["include"]))
        documents.append({"document_id": doc_id, "segment_ids": [r["id"] for r in group],
                          "final_segment_id": group[-1]["id"], "labels": final, "ever_seen": ever,
                          "behaviors": list(strongest.values()), "included_in_denominator": included,
                          "review_required": any(r["review_required"] for r in group)})
    included = [doc for doc in documents if doc["included_in_denominator"]]
    distributions = {}
    for name in config["dimensions"]:
        counts = Counter(value for doc in included for value in set(doc["labels"][name]))
        ever = Counter(value for doc in included for value in set(doc["ever_seen"][name]))
        distributions[name] = {"counts": dict(counts), "ever_seen_counts": dict(ever),
                               "rates": {label: n / len(included) for label, n in counts.items()}}
    actions = {category: {} for category in ("actual", "intent", "after_event_actual")}
    if "event" in config:
        actions["event_caused_actual"] = {}
    for category in actions:
        counts = Counter()
        for doc in included:
            found = set()
            for behavior in doc["behaviors"]:
                actual = behavior["stage"] in {"done", "ongoing"}
                selected = (actual if category == "actual" else
                            behavior["stage"] in {"planned", "considering"} if category == "intent" else
                            actual and behavior["time_scope"] == "after_event" if category == "after_event_actual" else
                            actual and behavior.get("event_link", {}).get("relation") == "caused_by")
                if behavior["actor"] == "self" and selected:
                    found.add(behavior["action"])
            counts.update(found)
        actions[category] = dict(counts)
    return documents, {"documents": len(documents), "segments": len(labeled),
                       "denominator_documents": len(included), "excluded_documents": len(documents) - len(included),
                       "review_documents": sum(doc["review_required"] for doc in documents),
                       "dimension_distributions": distributions, "behavior_document_counts": actions}


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
        reasons = (check_prediction_v2 if is_v2(config) else check_prediction)(prediction, row, config)
        result = {"id": row["id"], "input": row, "prediction": prediction,
                  "review_required": bool(reasons), "review_reasons": reasons,
                  "provenance": provenance}
        if is_v2(config):
            result["review_details"], result["field_status"] = review_details_v2(prediction, row, config, reasons)
        labeled.append(result)
    review = [row for row in labeled if row["review_required"]]
    summary = {"total": len(rows), "review_required": len(review),
               "auto_candidates": len(rows) - len(review),
               "auto_candidate_coverage": (len(rows) - len(review)) / len(rows),
               "semantic_accuracy": "not_evaluated", "provenance": provenance}
    documents = None
    if is_v2(config):
        documents, rollup = rollup_v2(labeled, config)
        summary.update(rollup)
        summary["review_field_counts"] = dict(Counter(d for row in labeled
            for d in {issue["field"] for issue in row["review_details"]}))
    else:
        summary["stance_counts"] = dict(Counter(row["prediction"]["stance"] for row in labeled))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    write_jsonl(out / "labels.jsonl", labeled)
    write_jsonl(out / "review.jsonl", review)
    if documents is not None:
        write_jsonl(out / "documents.jsonl", documents)
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
        require("stance" in row["prediction"],
                "evaluate currently supports v1 stance only; do not map v2 labels into v1")
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
