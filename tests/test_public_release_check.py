import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("public_release_check", ROOT / "scripts/public_release_check.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class PublicReleaseCheckTests(unittest.TestCase):
    def test_known_token_formats_are_blocked_and_redacted(self):
        openai = "sk-" + "1" * 24
        github = "ghp_" + "2" * 32
        findings = checker.scan_text(
            "OPENAI=" + openai + "\nKEY=" + github + "\n",
            "secret.txt",
        )
        self.assertEqual({item["kind"] for item in findings}, {"openai_key", "github_token"})
        self.assertNotIn("1" * 24, " ".join(item["detail"] for item in findings))

    def test_environment_lookup_and_placeholder_are_not_false_positive(self):
        self.assertEqual([], checker.scan_text(
            'API_KEY = os.getenv("API_KEY")\nTOKEN="example_token_value"', "config.py"))

    def test_private_files_block(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("SAFE=1", encoding="utf-8")
            report = checker.scan_repository(directory)
        self.assertFalse(report["passed"])
        self.assertEqual("private_config_file", report["findings"][0]["kind"])

    def test_clean_text_passes(self):
        self.assertEqual([], checker.scan_text("key = os.getenv('KEY')\n", "example.py"))


if __name__ == "__main__":
    unittest.main()
