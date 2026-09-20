import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "metrics_report", ROOT / "skills/ai-comment-labeler/scripts/metrics_report.py")
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


class MetricsReportTests(unittest.TestCase):
    def test_latency_and_cost_are_calculated_only_from_persisted_usage(self):
        results = [{"id": "a", "status": "candidate", "model_call_details": [
            {"status": "ok", "duration_ms": 10, "usage": {"input_tokens": 1000, "output_tokens": 200}}]},
                   {"id": "b", "status": "review", "model_call_details": [
                       {"status": "ok", "duration_ms": 30}]}]
        report = metrics.report(results, input_price_per_1k=2, output_price_per_1k=4)
        self.assertEqual(report["model_calls"], 2)
        self.assertEqual(report["latency_ms"]["p50"], 20)
        self.assertEqual(report["estimated_cost"], 2.8)

    def test_missing_usage_does_not_become_zero_cost_claim(self):
        result = metrics.report([{"status": "candidate", "model_calls": 1}])
        self.assertIsNone(result["estimated_cost"])
        self.assertIsNone(result["token_usage"]["input_tokens"])


if __name__ == "__main__":
    unittest.main()
