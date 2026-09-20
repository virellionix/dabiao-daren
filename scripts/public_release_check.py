#!/usr/bin/env python3
"""High-signal pre-publication checks for this repository.

The checker is deliberately conservative about credentials and private data,
but it does not claim to be a complete secret scanner.  It scans the files
that Git would include in a commit and, when requested, every reachable Git
revision.  A finding blocks the command; warnings are reported separately.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{24,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("credential_assignment", re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|secret|password|refresh[_-]?token)"
        r"\s*[:=]\s*[\"']?([A-Za-z0-9+/=_-]{24,})[\"']?",
        re.IGNORECASE,
    )),
)

PRIVATE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".sqlite", ".db"}
PRIVATE_DATA_SUFFIXES = {".xlsx", ".xls"}
PRIVATE_DIRS = {"data", "inputs", "outputs", ".runs"}
MAX_FILE_BYTES = 2_000_000


def _redact(value: str) -> str:
    value = value.replace("\n", " ")
    return value[:6] + "…" + value[-4:] if len(value) > 12 else "[redacted]"


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _finding(kind: str, path: str, line: int, detail: str, *, revision: str | None = None) -> dict:
    item = {"severity": "block", "kind": kind, "path": path, "line": line, "detail": detail}
    if revision:
        item["revision"] = revision
    return item


def scan_text(text: str, path: str, *, revision: str | None = None) -> list[dict]:
    """Return high-signal credential findings for text content."""
    findings = []
    for kind, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            # Documentation may contain the words "api_key" or an environment
            # lookup.  Only the assignment pattern needs this false-positive
            # guard; concrete token formats remain blockers.
            if kind == "credential_assignment":
                value = match.group(1)
                lowered = value.lower()
                if lowered in {"placeholder", "your_api_key_here", "example_token_value"}:
                    continue
                before = text[max(0, match.start() - 40):match.start()].lower()
                if "getenv" in before or "environment" in before:
                    continue
                detail = "credential-like assignment: " + _redact(value)
            else:
                detail = kind + ": " + _redact(match.group(0))
            findings.append(_finding(kind, path, _line_number(text, match.start()), detail,
                                     revision=revision))
    return findings


def _path_finding(path: str, *, revision: str | None = None) -> list[dict]:
    file_path = Path(path)
    name = file_path.name.lower()
    parts = {part.lower() for part in file_path.parts}
    findings = []
    if name in {".env", ".env.local", ".env.production", ".env.development"}:
        findings.append(_finding("private_config_file", path, 1,
                                 "environment configuration is not safe to publish", revision=revision))
    if file_path.suffix.lower() in PRIVATE_SUFFIXES:
        findings.append(_finding("private_file", path, 1,
                                 "private key/database file is not safe to publish", revision=revision))
    if file_path.suffix.lower() in PRIVATE_DATA_SUFFIXES:
        findings.append(_finding("private_workbook", path, 1,
                                 "workbook may contain business or personal data; keep it outside the public repository",
                                 revision=revision))
    if parts & PRIVATE_DIRS:
        findings.append(_finding("private_data_path", path, 1,
                                 "data/input/output run directory is not a public source artifact", revision=revision))
    return findings


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.stdout


def candidate_files(root: Path) -> list[str]:
    """Files tracked or untracked-but-not-ignored by Git."""
    try:
        raw = _git(root, "ls-files", "-co", "--exclude-standard")
        return [line for line in raw.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        return [str(path.relative_to(root)) for path in root.rglob("*")
                if path.is_file() and ".git" not in path.parts]


def scan_repository(root: str | Path, *, history: bool = False) -> dict:
    root = Path(root).resolve()
    findings = []
    scanned = []
    for relative in candidate_files(root):
        path = root / relative
        findings.extend(_path_finding(relative))
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            data = path.read_bytes()
            if b"\x00" in data:
                continue
            scanned.append(relative)
            findings.extend(scan_text(data.decode("utf-8"), relative))
        except (OSError, UnicodeDecodeError):
            continue

    history_revisions = 0
    if history:
        try:
            revisions = [line for line in _git(root, "rev-list", "--all").splitlines() if line]
        except (OSError, subprocess.CalledProcessError):
            revisions = []
        for revision in revisions:
            history_revisions += 1
            try:
                paths = [line for line in _git(root, "ls-tree", "-r", "--name-only", revision).splitlines() if line]
            except (OSError, subprocess.CalledProcessError):
                continue
            for relative in paths:
                findings.extend(_path_finding(relative, revision=revision))
                try:
                    data = subprocess.run(["git", "-C", str(root), "show", f"{revision}:{relative}"],
                                          check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
                    if len(data) > MAX_FILE_BYTES or b"\x00" in data:
                        continue
                    findings.extend(scan_text(data.decode("utf-8"), relative, revision=revision))
                except (OSError, UnicodeDecodeError, subprocess.CalledProcessError):
                    continue
    return {"root": str(root), "scanned_files": len(scanned),
            "history_revisions": history_revisions, "findings": findings,
            "passed": not findings}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--history", action="store_true", help="scan all reachable Git revisions")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)
    report = scan_repository(args.root, history=args.history)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("public release scan: " + ("PASS" if report["passed"] else "BLOCKED"))
        print(f"scanned files={report['scanned_files']} history revisions={report['history_revisions']}")
        for item in report["findings"]:
            revision = f" @{item['revision'][:8]}" if item.get("revision") else ""
            print(f"- {item['path']}:{item['line']}{revision} [{item['kind']}] {item['detail']}")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
