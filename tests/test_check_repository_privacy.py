import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_repository_privacy.py"


class RepositoryPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.repository = Path(self.box.name)
        subprocess.run(
            ["git", "init", "--quiet", str(self.repository)],
            check=True,
            capture_output=True,
            text=True,
        )

    def write(self, relative_path, contents):
        path = self.repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, bytes):
            path.write_bytes(contents)
        else:
            path.write_text(contents, encoding="utf-8")
        return path

    def track(self, *relative_paths):
        subprocess.run(
            ["git", "add", "--", *relative_paths],
            cwd=self.repository,
            check=True,
            capture_output=True,
            text=True,
        )

    def run_scanner(self, repository=None):
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repository",
                str(repository or self.repository),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        records = []
        for line in process.stdout.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                self.fail(f"scanner emitted non-JSON output: {error}: {line!r}")
            self.assertEqual(set(record), {"path", "line", "category"})
            records.append(record)
        return process, records

    def test_reports_sensitive_categories_with_line_numbers_without_values(self):
        private_path = "/Users" + "/casey/Documents/application.md"
        credential_value = "sk-live-" + "A" * 24
        email = "casey.private" + "@" + "gmail.com"
        phone = "+61 " + "412 345 678"
        credential_line = "api_" + f"key = {credential_value}\n"
        self.write(
            "notes.txt",
            "heading\n"
            + f"source: {private_path}\n"
            + credential_line
            + f"email: {email}\n"
            + f"mobile: {phone}\n",
        )
        self.track("notes.txt")

        process, records = self.run_scanner()

        self.assertEqual(process.returncode, 1)
        self.assertEqual(
            records,
            [
                {"path": "notes.txt", "line": 2, "category": "private_absolute_path"},
                {"path": "notes.txt", "line": 3, "category": "credential_or_secret"},
                {"path": "notes.txt", "line": 4, "category": "personal_email"},
                {"path": "notes.txt", "line": 5, "category": "personal_phone"},
            ],
        )
        disclosed = process.stdout + process.stderr
        for sensitive_value in (private_path, credential_value, email, phone):
            self.assertNotIn(sensitive_value, disclosed)

    def test_reports_private_key_material_as_a_secret(self):
        private_key_marker = "-----BEGIN " + "PRIVATE KEY-----\n"
        self.write("config.txt", private_key_marker)
        self.track("config.txt")

        process, records = self.run_scanner()

        self.assertEqual(process.returncode, 1)
        self.assertEqual(
            records,
            [{"path": "config.txt", "line": 1, "category": "credential_or_secret"}],
        )

    def test_reports_prefixed_credential_variable_assignments(self):
        openai_value = "live-openai-" + "A" * 24
        aws_value = "live-aws-" + "B" * 32
        self.write(
            "config.env",
            f"{'OPENAI_' + 'API_KEY'}={openai_value}\n"
            f"{'AWS_SECRET_' + 'ACCESS_KEY'}={aws_value}\n",
        )
        self.track("config.env")

        process, records = self.run_scanner()

        self.assertEqual(process.returncode, 1)
        self.assertEqual(
            records,
            [
                {
                    "path": "config.env",
                    "line": 1,
                    "category": "credential_or_secret",
                },
                {
                    "path": "config.env",
                    "line": 2,
                    "category": "credential_or_secret",
                },
            ],
        )
        disclosed = process.stdout + process.stderr
        self.assertNotIn(openai_value, disclosed)
        self.assertNotIn(aws_value, disclosed)

    def test_reports_root_home_absolute_path(self):
        private_path = "/" + "root/private/application.md"
        self.write("notes.txt", f"source: {private_path}\n")
        self.track("notes.txt")

        process, records = self.run_scanner()

        self.assertEqual(process.returncode, 1)
        self.assertEqual(
            records,
            [
                {
                    "path": "notes.txt",
                    "line": 1,
                    "category": "private_absolute_path",
                }
            ],
        )
        self.assertNotIn(private_path, process.stdout + process.stderr)

    def test_reports_private_workspace_artifact_path_at_line_one(self):
        self.write(
            "private-workspace/profile/candidate-profile.yaml",
            "candidate_id: synthetic-candidate-001\n",
        )
        self.track("private-workspace/profile/candidate-profile.yaml")

        process, records = self.run_scanner()

        self.assertEqual(process.returncode, 1)
        self.assertEqual(
            records,
            [
                {
                    "path": "private-workspace/profile/candidate-profile.yaml",
                    "line": 1,
                    "category": "workspace_private_artifact",
                }
            ],
        )

    def test_reports_resume_artifact_path_at_line_one(self):
        self.write("documents/resume.txt", "Synthetic resume fixture.\n")
        self.track("documents/resume.txt")

        process, records = self.run_scanner()

        self.assertEqual(process.returncode, 1)
        self.assertEqual(
            records,
            [
                {
                    "path": "documents/resume.txt",
                    "line": 1,
                    "category": "workspace_private_artifact",
                }
            ],
        )

    def test_reports_env_local_and_markdown_resume_artifacts(self):
        self.write("config/.env.local", "MODE=development\n")
        self.write("documents/resume.md", "Synthetic resume fixture.\n")
        self.track("config/.env.local", "documents/resume.md")

        process, records = self.run_scanner()

        self.assertEqual(process.returncode, 1)
        self.assertEqual(
            records,
            [
                {
                    "path": "config/.env.local",
                    "line": 1,
                    "category": "workspace_private_artifact",
                },
                {
                    "path": "documents/resume.md",
                    "line": 1,
                    "category": "workspace_private_artifact",
                },
            ],
        )

    def test_allows_exact_public_resume_reference(self):
        self.write(
            "references/resume-and-cover-letter.md",
            "Public workflow documentation.\n",
        )
        self.track("references/resume-and-cover-letter.md")

        process, records = self.run_scanner()

        self.assertEqual(process.returncode, 0)
        self.assertEqual(records, [])

    def test_resume_reference_descendant_is_not_allowlisted(self):
        descendant = "references/resume-and-cover-letter.md/private/resume.md"
        self.write(descendant, "Private resume artifact.\n")
        self.track(descendant)

        process, records = self.run_scanner()

        self.assertEqual(process.returncode, 1)
        self.assertEqual(
            records,
            [
                {
                    "path": descendant,
                    "line": 1,
                    "category": "workspace_private_artifact",
                }
            ],
        )

    def test_ignores_untracked_files(self):
        self.write("tracked.txt", "Public documentation.\n")
        self.track("tracked.txt")
        untracked_value = "ghp_" + "z" * 36
        self.write("untracked.txt", f"token = {untracked_value}\n")

        process, records = self.run_scanner()

        self.assertEqual(process.returncode, 0)
        self.assertEqual(records, [])
        self.assertNotIn(untracked_value, process.stdout + process.stderr)

    def test_skips_tracked_binary_files(self):
        binary_value = "AKIA" + "A" * 16
        self.write(
            "documents/resume.pdf",
            b"%PDF\x00" + binary_value.encode("ascii"),
        )
        self.track("documents/resume.pdf")

        process, records = self.run_scanner()

        self.assertEqual(process.returncode, 0)
        self.assertEqual(records, [])
        self.assertNotIn(binary_value, process.stdout + process.stderr)

    def test_allows_approved_synthetic_and_documentation_examples(self):
        documentation = "\n".join(
            (
                "contact: candidate@example.com",
                "candidate_id: synthetic-candidate-001",
                "api_key: YOUR_API_KEY",
                "password: ${PASSWORD}",
                "token: <REDACTED>",
                "phone: 0000 000 000",
                "repository: /path/to/repository",
            )
        )
        self.write("docs/examples.md", documentation + "\n")
        self.write(
            "assets/workspace-template/candidate-profile.example.yaml",
            "candidate_id: synthetic-example-001\n",
        )
        self.track(
            "docs/examples.md",
            "assets/workspace-template/candidate-profile.example.yaml",
        )

        process, records = self.run_scanner()

        self.assertEqual(process.returncode, 0)
        self.assertEqual(records, [])

    def test_scan_error_is_redacted_and_nonzero(self):
        outside_box = tempfile.TemporaryDirectory()
        self.addCleanup(outside_box.cleanup)
        not_a_repository = Path(outside_box.name)

        process, records = self.run_scanner(not_a_repository)

        self.assertEqual(process.returncode, 1)
        self.assertEqual(
            records,
            [{"path": ".", "line": 1, "category": "scan_error"}],
        )
        self.assertNotIn(str(not_a_repository), process.stdout + process.stderr)

    def test_clean_tracked_text_returns_zero(self):
        self.write("README.md", "Synthetic public repository fixture.\n")
        self.track("README.md")

        process, records = self.run_scanner()

        self.assertEqual(process.returncode, 0)
        self.assertEqual(records, [])

    def test_scans_project_repository_without_scan_error(self):
        process, records = self.run_scanner(ROOT)

        self.assertIn(process.returncode, (0, 1))
        self.assertNotIn("scan_error", {record["category"] for record in records})
        self.assertEqual(process.stderr, "")


if __name__ == "__main__":
    unittest.main()
