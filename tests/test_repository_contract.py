import pathlib
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]


class RepositoryContractTests(unittest.TestCase):
    def test_required_files_exist(self):
        required_files = (
            "SKILL.md",
            "README.md",
            "LICENSE",
            "CHANGELOG.md",
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
        self.assertTrue(contents.startswith("---\n"), "SKILL.md must start with YAML frontmatter")
        _, frontmatter_text, _ = contents.split("---", 2)
        frontmatter = yaml.safe_load(frontmatter_text)

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
