import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]


def parse_skill_frontmatter(contents):
    lines = contents.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError("SKILL.md must start with a standalone YAML delimiter line")

    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            return yaml.safe_load("".join(lines[1:index]))

    raise ValueError("SKILL.md must have a standalone closing YAML delimiter line")


class RepositoryContractTests(unittest.TestCase):
    def test_required_files_exist(self):
        required_files = (
            "SKILL.md",
            "README.md",
            "LICENSE",
            "CHANGELOG.md",
            "requirements.txt",
            "agents/openai.yaml",
        )

        for relative_path in required_files:
            with self.subTest(path=relative_path):
                self.assertTrue(
                    (ROOT / relative_path).is_file(),
                    f"missing required file: {relative_path}",
                )

    def test_skill_frontmatter(self):
        skill_path = ROOT / "SKILL.md"
        self.assertTrue(skill_path.is_file(), "missing required file: SKILL.md")

        contents = skill_path.read_text(encoding="utf-8")
        frontmatter = parse_skill_frontmatter(contents)

        self.assertEqual(
            frontmatter,
            {
                "name": "applying-for-jobs-in-australia",
                "description": (
                    "Use for evidence-based Australian job discovery, resume and "
                    "cover-letter preparation, assisted applications, and consent-based "
                    "job-search tracking for onshore or offshore candidates across "
                    "professional and casual or part-time work."
                ),
            },
        )
        self.assertIn("Australia", frontmatter["description"])

    def test_skill_frontmatter_rejects_malformed_closing_delimiter(self):
        contents = "---\nname: example\n---garbage\n# Example\n"

        with self.assertRaisesRegex(ValueError, "standalone closing"):
            parse_skill_frontmatter(contents)

    def test_repository_identity_files_are_nonempty(self):
        identity_markers = {
            "README.md": "Applying for Jobs in Australia",
            "CHANGELOG.md": "# Changelog",
            "LICENSE": "MIT License",
        }

        for relative_path, marker in identity_markers.items():
            with self.subTest(path=relative_path):
                contents = (ROOT / relative_path).read_text(encoding="utf-8").strip()
                self.assertTrue(contents, f"{relative_path} must not be blank")
                self.assertIn(marker, contents)

    def test_skill_preserves_task_two_behavioral_shell(self):
        contents = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        required_rules = (
            "Never fabricate, inflate, or silently infer candidate facts",
            "Apply an Evidence Audit to every material candidate claim and screening decision",
            "always stop before the final **Submit** action. The candidate performs submission.",
            "Morning discovery and evening check-in automation each require separate explicit consent.",
            "Anonymous cross-user analytics requires another separate explicit opt-in",
            (
                "Do not place a role in Top N or begin Application Preparation until a dated "
                "JD snapshot records the source URL and retrieval time."
            ),
            "Maintain both the tracker's current state and an append-only event log.",
            (
                "Candidate data belongs in the private local workspace and must never be "
                "committed to the public skill repository."
            ),
        )

        for rule in required_rules:
            with self.subTest(rule=rule):
                self.assertIn(rule, contents)

    def test_openai_interface_metadata(self):
        metadata_path = ROOT / "agents" / "openai.yaml"
        self.assertTrue(
            metadata_path.is_file(),
            "missing required file: agents/openai.yaml",
        )

        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        interface = metadata["interface"]
        short_description = interface["short_description"]

        self.assertIn("display_name", interface)
        self.assertEqual(
            interface["display_name"],
            "Applying for Jobs in Australia",
        )
        self.assertGreaterEqual(len(short_description), 25)
        self.assertLessEqual(len(short_description), 64)
        self.assertIn(
            "$applying-for-jobs-in-australia",
            interface["default_prompt"],
        )


if __name__ == "__main__":
    unittest.main()
