"""Model-neutral shared entry point. No built-in network, credentials or retries."""

import copy
import importlib.util
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("shared_label_io", ROOT / "scripts/label_io.py")
io = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(io)


def resolve_project(project, catalog=()):
    """Use an explicit project target or a unique, explicitly owned catalog target."""
    project = copy.deepcopy(project)
    io.require(isinstance(project, dict), "project must be object")
    io.require(isinstance(catalog, (list, tuple)) and len(catalog) <= 10000, "invalid catalog")
    owned = {}
    for item in catalog:
        io.keys(item, {"target", "aliases", "is_own"})
        io.text(item["target"], "catalog target", 200)
        io.string_list(item["aliases"], "catalog aliases")
        io.require(type(item["is_own"]) is bool, "catalog is_own must be boolean")
        if item["is_own"]:
            owned.setdefault(item["target"], []).extend(item["aliases"])
    explicit = project.get("target_brand")
    if explicit:
        io.require(not owned or set(owned) == {explicit}, "project/catalog target conflict")
        origin = "explicit_project"
    else:
        io.require(len(owned) == 1, "cannot determine a unique target from supplied project/catalog")
        project["target_brand"] = next(iter(owned))
        origin = "explicit_catalog_ownership"
    aliases = project.get("aliases", [])
    io.string_list(aliases, "aliases")
    project["aliases"] = list(dict.fromkeys(aliases + owned.get(project["target_brand"], [])))
    return io.project_config(project), {"source": origin, "target_brand": project["target_brand"]}


def adapt_record(row, *, id_column, text_column, context_columns=None, source_kind="full_text"):
    """Adapt a dict-like legacy row; caller states whether the source is complete."""
    item_id, text = row.get(id_column), row.get(text_column)
    io.text(item_id, "source id", 200)
    io.text(text, "source text")
    context = {}
    for kind, column in (context_columns or {}).items():
        io.choices(kind, io.CONTEXT, "context column")
        value = row.get(column)
        if value is not None and value != "":
            io.text(value, "context text")
            context[kind] = value
    return {"id": item_id, "document_id": item_id, "segment_index": 1,
            "is_last_segment": True, "source_kind": source_kind, "text": text, "context": context}


class SharedLabeler:
    def __init__(self, project, model, *, model_id, catalog=(), context_resolver=None):
        self.project, self.scope = resolve_project(project, catalog)
        io.require(io.is_v2(self.project) and self.project["unit"] == "item",
                   "shared entry currently requires a v2 item project; use label_io for segments")
        io.require(callable(model), "model callback required")
        io.require(context_resolver is None or callable(context_resolver), "invalid resolver")
        io.text(model_id, "model_id", 200)
        self.model, self.resolver = model, context_resolver
        self.policy = (ROOT / "references/labeling-policy.md").read_text(encoding="utf-8")
        self.contract = (ROOT / "references/rule-packs.md").read_text(encoding="utf-8")
        self.provenance = {
            "model": model_id, "rule_version": self.project["rule_version"],
            "project_sha256": io.digest(io.encode(self.project)),
            "policy_sha256": io.digest(self.policy), "contract_sha256": io.digest(self.contract),
            "core_sha256": io.digest(Path(__file__).read_text(encoding="utf-8")),
            "helper_sha256": io.digest((ROOT / "scripts/label_io.py").read_text(encoding="utf-8")),
            "semantic_accuracy": "not_measured", "scope_resolution": self.scope,
        }

    def label(self, record, *, context_refs=None):
        """At most two model calls and one context round; no implicit retry."""
        row = copy.deepcopy(record)
        refs = copy.deepcopy(context_refs or {})
        io.require(io.digest(io.encode(self.project)) == self.provenance["project_sha256"],
                   "project changed after initialization; create a new labeler for the new version")
        io.inputs([row], self.project)
        io.keys(refs, set(), io.CONTEXT)
        for ref in refs.values():
            io.text(ref, "caller-bound context reference", 500)
        trace, history, details, call_details = [], [], [], []
        calls = 0

        def result(status, prediction=None, extra=(), fields=None):
            return {"id": row["id"], "status": status, "input": copy.deepcopy(row),
                    "prediction": prediction, "review_details": details + list(extra),
                    "field_status": fields or {}, "context_trace": trace,
                    "model_calls": calls, "model_call_details": copy.deepcopy(call_details),
                    "provenance": copy.deepcopy(self.provenance)}

        for turn in range(2):
            request = {
                "policy": self.policy, "output_contract": self.contract,
                "project": copy.deepcopy(self.project), "record": copy.deepcopy(row),
                "context_sources": sorted(refs), "context_trace": copy.deepcopy(trace),
                "history": copy.deepcopy(history), "can_request_context": turn == 0,
                "response_protocol": {
                    "final": {"type": "prediction", "prediction": "v2 candidate JSON object"},
                    "context": {"type": "context_request", "sources": ["parent_comment"],
                                "reason": "Explain the missing information; source names only, no URLs/IDs"},
                },
            }
            io.require(len(io.encode(request)) <= 150000, "model request exceeds character budget")
            try:
                calls += 1
                started = time.perf_counter()
                answer = self.model(copy.deepcopy(request))
            except Exception:
                call_details.append({"status": "error",
                                     "duration_ms": round((time.perf_counter() - started) * 1000, 3)})
                return result("error", extra=[{"field": "record", "reason": "model_call_failed",
                                               "detail": "模型调用失败；未自动重试，未生成默认标签。"}])
            call_details.append({"status": "ok",
                                 "duration_ms": round((time.perf_counter() - started) * 1000, 3)})
            usage = getattr(self.model, "last_usage", None)
            if isinstance(usage, dict):
                call_details[-1]["usage"] = copy.deepcopy(usage)
            try:
                if isinstance(answer, str):
                    io.require(len(answer) <= io.MAX_LINE, "model output exceeds limit")
                    answer = io.parse(answer)
                else:
                    io.require(len(io.encode(answer)) <= io.MAX_LINE, "model output exceeds limit")
                    answer = io.parse(io.encode(answer))
                io.require(isinstance(answer, dict), "model output must be object")
                if answer.get("type") == "prediction":
                    io.keys(answer, {"type", "prediction"})
                    pred = answer["prediction"]
                    reasons = io.check_prediction_v2(pred, row, self.project)
                    located, fields = io.review_details_v2(pred, row, self.project, reasons)
                    for dimension, spec in self.project["dimensions"].items():
                        values = pred["labels"][dimension]
                        if any(e["source"] in {"image_ocr", "image_description"}
                               for v in values for e in v["evidence"]):
                            located.append({"field": "labels." + dimension, "reason": "derived_image_evidence",
                                            "detail": "使用图像转写/描述，需核对原图；未证明图像内容正确。"})
                    if any(e["source"] in {"image_ocr", "image_description"}
                           for e in pred["evidence"] + [e for b in pred["behaviors"]
                               for e in b["evidence"] + b.get("event_link", {}).get("evidence", [])]):
                        located.append({"field": "source", "reason": "derived_image_evidence",
                                        "detail": "总体判断或行为使用了图像转写/描述，需要核对原图。"})
                    for detail in details + located:
                        if detail["field"] in fields:
                            fields[detail["field"]] = "review"
                    return result("review" if details or located else "candidate", pred, located, fields)
                io.keys(answer, {"type", "sources", "reason"})
                io.choices(answer["type"], {"context_request"}, "response type")
                io.string_list(answer["sources"], "context sources", io.CONTEXT, nonempty=True)
                io.require(len(answer["sources"]) <= 4, "request at most four context sources")
                io.text(answer["reason"], "context reason", 400)
            except (ValueError, TypeError, KeyError, RecursionError):
                return result("error", extra=[{"field": "record", "reason": "invalid_model_output",
                                               "detail": "模型输出未满足约定；没有转换成中性或正常标签。"}])
            if turn:
                return result("review", extra=[{"field": "source", "reason": "context_limit",
                                                "detail": "一次补查后仍需上下文；停止，不循环调用。"}])
            history.append(answer)
            for kind in answer["sources"]:
                # The model selects a kind only. The caller binds the actual source.
                if row.get("context", {}).get(kind):
                    trace.append({"kind": kind, "status": "already_provided"})
                    continue
                bound = {"kind": kind, "record_id": row["id"], "source_ref": refs.get(kind)}
                status, payload = "unavailable", None
                if kind in refs and self.resolver is not None:
                    try:
                        payload = self.resolver(copy.deepcopy(bound))
                        io.keys(payload, set(bound) | {"status"}, {"text"})
                        io.require(all(payload[k] == v for k, v in bound.items()), "context binding mismatch")
                        io.choices(payload["status"], {"ok", "not_found", "permission_denied", "unavailable"},
                                   "context status")
                        status = payload["status"]
                        if status == "ok":
                            io.text(payload.get("text"), "context text")
                            io.require(sum(len(v) for v in row.get("context", {}).values()) +
                                       len(payload["text"]) <= 30000, "context budget exceeded")
                            row.setdefault("context", {})[kind] = payload["text"]
                    except Exception:
                        status = "invalid_or_failed"
                entry = dict(bound, status=status)
                if status == "ok":
                    entry["text_sha256"] = io.digest(payload["text"])
                else:
                    details.append({"field": "source", "reason": "context_" + status,
                                    "detail": kind + " 未补齐；保留复核，不静默当成无关或中性。"})
                trace.append(entry)
        raise AssertionError("unreachable")

    def export_columns(self, result, columns):
        """Map dimensions to legacy output columns; status is always retained."""
        io.require(result.get("provenance", {}).get("project_sha256") ==
                   io.digest(io.encode(self.project)), "result/project mismatch")
        io.require(isinstance(columns, dict) and columns, "output columns required")
        io.require(len(set(columns.values())) == len(columns), "duplicate output column")
        reserved = {"打标状态", "复核原因"}
        out = {}
        for dimension, column in columns.items():
            io.choices(dimension, self.project["dimensions"], "export dimension")
            io.text(column, "output column", 100)
            io.require(column not in reserved, "reserved output column")
            pred = result["prediction"]
            spec = self.project["dimensions"][dimension]
            out[column] = ("、".join(spec["labels"][v["value"]]["name"]
                                   for v in pred["labels"][dimension]) if pred is not None else None)
        out["打标状态"] = result["status"]
        out["复核原因"] = "；".join(d["detail"] for d in result["review_details"])
        return out
