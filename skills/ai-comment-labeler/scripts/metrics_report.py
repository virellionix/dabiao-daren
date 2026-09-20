#!/usr/bin/env python3
"""Summarize latency, token usage and batch outcomes from a completed run.

The reporter is model-agnostic. Token counts are reported only when the
calling program persisted usage metadata; no token or price is guessed.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def _read_rows(path):
    path = Path(path)
    if path.is_dir():
        rows = []
        for child in sorted(path.glob("*.jsonl")):
            rows.extend(_read_rows(child))
        return rows
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _percentile(values, percentile):
    if not values:
        return None
    values = sorted(values)
    rank = (len(values) - 1) * percentile
    lower, upper = math.floor(rank), math.ceil(rank)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (rank - lower)


def _usage(result):
    usage = result.get("usage")
    if isinstance(usage, dict):
        return usage
    details = result.get("model_call_details", [])
    if isinstance(details, list):
        merged = {}
        for detail in details:
            if isinstance(detail, dict) and isinstance(detail.get("usage"), dict):
                for key, value in detail["usage"].items():
                    if isinstance(value, (int, float)):
                        merged[key] = merged.get(key, 0) + value
        return merged
    return {}


def _tokens(usage, names):
    for name in names:
        value = usage.get(name)
        if isinstance(value, (int, float)):
            return value
    return 0


def report(results, *, run=None, input_price_per_1k=None, output_price_per_1k=None):
    statuses = {"candidate": 0, "review": 0, "error": 0}
    durations, input_tokens, output_tokens = [], 0, 0
    records_with_usage = 0
    calls = 0
    for result in results:
        status = result.get("status")
        if status not in statuses:
            status = "review" if result.get("review_required") else ("candidate" if result.get("prediction") else "error")
        statuses[status] += 1
        details = result.get("model_call_details")
        if isinstance(details, list):
            for detail in details:
                if isinstance(detail, dict):
                    calls += 1
                    if isinstance(detail.get("duration_ms"), (int, float)):
                        durations.append(detail["duration_ms"])
        elif isinstance(result.get("model_calls"), int):
            calls += result["model_calls"]
        usage = _usage(result)
        if usage:
            records_with_usage += 1
            input_tokens += _tokens(usage, ("input_tokens", "prompt_tokens"))
            output_tokens += _tokens(usage, ("output_tokens", "completion_tokens"))
    cost = None
    if input_price_per_1k is not None and output_price_per_1k is not None and records_with_usage:
        cost = (input_tokens / 1000 * input_price_per_1k + output_tokens / 1000 * output_price_per_1k)
    report = {
        "records": len(results), "statuses": statuses, "model_calls": calls,
        "latency_ms": {"count": len(durations), "min": min(durations) if durations else None,
                       "average": sum(durations) / len(durations) if durations else None,
                       "p50": _percentile(durations, .50), "p95": _percentile(durations, .95),
                       "max": max(durations) if durations else None},
        "token_usage": {"records_with_usage": records_with_usage,
                        "input_tokens": input_tokens if records_with_usage else None,
                        "output_tokens": output_tokens if records_with_usage else None},
        "estimated_cost": cost,
        "cost_basis": ("caller supplied prices per 1K input/output tokens"
                       if cost is not None else "unavailable: persist usage and supply both prices"),
        "semantic_accuracy": "not_measured",
    }
    if run:
        manifest = json.loads((Path(run) / "manifest.json").read_text(encoding="utf-8"))
        report["batching"] = {"batches": manifest.get("batches"), "total": manifest.get("total"),
                              "batch_size_sha256": manifest.get("batch_sha256")}
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True)
    parser.add_argument("--run")
    parser.add_argument("--input-price-per-1k", type=float)
    parser.add_argument("--output-price-per-1k", type=float)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    payload = json.dumps(report(_read_rows(args.results), run=args.run,
                                input_price_per_1k=args.input_price_per_1k,
                                output_price_per_1k=args.output_price_per_1k),
                         ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
