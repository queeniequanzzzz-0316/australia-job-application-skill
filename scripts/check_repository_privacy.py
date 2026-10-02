#!/usr/bin/env python3
"""Scan Git-tracked text files for repository privacy violations."""

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


PRIVATE_ABSOLUTE_PATH = re.compile(
    r"(?<![\w.])(?:/(?:Users|home|private|root)/[^\s<>'\"]+|[A-Za-z]:\\Users\\[^\s<>'\"]+)",
    re.IGNORECASE,
)
EMAIL = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+)@([A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.IGNORECASE)
KNOWN_CREDENTIAL_PATTERN = re.compile(
    r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:live|prod)-[A-Za-z0-9_-]{12,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)
SECRET_ASSIGNMENT = re.compile(
    r"\b(?:[A-Za-z0-9]+[_-]+)*(?:api[_-]?key|client[_-]?secret|secret[_-]?access[_-]?key"
    r"|access[_-]?token|auth[_-]?token|password|passwd|secret)"
    r"\b\s*[:=]\s*[\"']?([^\s\"',;\]]+)",
    re.IGNORECASE,
)
PHONE = re.compile(
    r"(?<!\w)(?:"
    r"\+[1-9](?:[\s().-]*\d){7,14}"
    r"|0[2-478](?:[\s().-]*\d){8}"
    r"|\d{3}[-. ]\d{3}[-. ]\d{4}"
    r")(?!\w)"
)

EXAMPLE_EMAIL_DOMAINS = {"example.com", "example.net", "example.org"}
PLACEHOLDER_SECRETS = {
    "api_key",
    "changeme",
    "example",
    "placeholder",
    "redacted",
    "secret",
    "test",
    "token",
    "your_api_key",
    "your_secret",
    "your_token",
}
PRIVATE_WORKSPACE_COMPONENTS = {"private-workspace", "private_workspace", "workspace"}
PRIVATE_ARTIFACT_NAMES = {
    ".env",
    "applications.csv",
    "candidate-profile.yaml",
    "candidate-profile.yml",
    "events.jsonl",
}
APPROVED_SYNTHETIC_ROOTS = (
    ("assets", "workspace-template"),
    ("evals", "fixtures"),
    ("evals", "task9"),
)
APPROVED_PUBLIC_ARTIFACTS = {("references", "resume-and-cover-letter.md")}


def _record(path, line, category):
    return {"path": path, "line": line, "category": category}


def _is_placeholder_secret(value):
    normalized = value.strip("<>[](){}$\"'").lower()
    return (
        normalized in PLACEHOLDER_SECRETS
        or normalized.startswith(("example-", "fictional-", "synthetic-", "test-"))
        or (value.startswith(("${", "{{", "{")) and value.endswith(("}", "}}")))
    )


def _contains_secret(line):
    if KNOWN_CREDENTIAL_PATTERN.search(line):
        return True
    return any(
        not _is_placeholder_secret(match.group(1))
        for match in SECRET_ASSIGNMENT.finditer(line)
    )


def _contains_personal_email(line):
    return any(
        match.group(2).lower() not in EXAMPLE_EMAIL_DOMAINS
        for match in EMAIL.finditer(line)
    )


def _contains_personal_phone(line):
    for match in PHONE.finditer(line):
        digits = re.sub(r"\D", "", match.group(0))
        if digits and set(digits) != {"0"}:
            return True
    return False


def _is_private_artifact(relative_path):
    path = PurePosixPath(relative_path)
    parts = tuple(part.lower() for part in path.parts)
    if parts in APPROVED_PUBLIC_ARTIFACTS:
        return False
    if any(parts[: len(root)] == root for root in APPROVED_SYNTHETIC_ROOTS):
        return False
    if any(part in PRIVATE_WORKSPACE_COMPONENTS for part in parts[:-1]):
        return True
    if parts[-1] in PRIVATE_ARTIFACT_NAMES or parts[-1].startswith(".env."):
        return True
    return bool(
        re.fullmatch(
            r"(?:resume|cover[-_ ]?letter)(?:[-_ ].*)?\.(?:docx?|md|pdf|txt)",
            parts[-1],
        )
    )


def _tracked_paths(repository):
    result = subprocess.run(
        ["git", "-C", os.fspath(repository), "ls-files", "-z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Git tracked-file inventory failed")
    paths = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    return sorted(path for path in paths if path)


def _read_tracked_file(repository, relative_path):
    path = repository / relative_path
    if path.is_symlink():
        return os.readlink(path).encode("utf-8", errors="surrogateescape")
    return path.read_bytes()


def scan(repository):
    findings = []
    try:
        tracked_paths = _tracked_paths(repository)
    except (OSError, RuntimeError):
        return [_record(".", 1, "scan_error")]

    for relative_path in tracked_paths:
        try:
            contents = _read_tracked_file(repository, relative_path)
        except OSError:
            findings.append(_record(relative_path, 1, "scan_error"))
            continue

        if b"\0" in contents:
            continue
        try:
            text = contents.decode("utf-8")
        except UnicodeDecodeError:
            continue

        if _is_private_artifact(relative_path):
            findings.append(_record(relative_path, 1, "workspace_private_artifact"))

        for line_number, line in enumerate(text.splitlines(), start=1):
            categories = []
            if PRIVATE_ABSOLUTE_PATH.search(line):
                categories.append("private_absolute_path")
            if _contains_secret(line):
                categories.append("credential_or_secret")
            if _contains_personal_email(line):
                categories.append("personal_email")
            if _contains_personal_phone(line):
                categories.append("personal_phone")
            findings.extend(
                _record(relative_path, line_number, category)
                for category in categories
            )

    return sorted(
        findings,
        key=lambda finding: (finding["path"], finding["line"], finding["category"]),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Scan Git-tracked text files for private repository data."
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path.cwd(),
        help="Git repository to scan (default: current directory)",
    )
    arguments = parser.parse_args(argv)

    findings = scan(arguments.repository)
    for finding in findings:
        print(json.dumps(finding, sort_keys=True))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
