"""Behavioral fixture contract for SCR-04 Top N shortfall."""

import json
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "evals" / "screening-cases.md"
FIXTURE = ROOT / "evals" / "fixtures" / "scr-04-workspace"
DISCOVERY = FIXTURE / "jobs" / "role-records.json"

ORIGINAL_REQUIRED = (
    "Return the four qualifying roles and disclose that the requested count could not be met."
)
ORIGINAL_PROHIBITED = (
    "Do not pad the list with failed, duplicate, stale, or weakly evidenced roles."
)
ORIGINAL_PASS = "Top N contains exactly the four passing roles and records the shortfall."


def scr04_section():
    text = SCENARIOS.read_text(encoding="utf-8")
    return text.split("## SCR-04: Fewer suitable roles than Top N\n", 1)[1].split(
        "\n## SCR-05:", 1
    )[0]


def field(section, label):
    prefix = f"- **{label}:** "
    matches = [line[len(prefix):] for line in section.splitlines() if line.startswith(prefix)]
    if len(matches) != 1:
        raise AssertionError(f"SCR-04 must contain exactly one {label} field")
    return matches[0]


class Scr04BehavioralFixtureTests(unittest.TestCase):
    def test_original_contract_is_unchanged_and_points_to_fixture(self):
        section = scr04_section()
        self.assertIn("Synthetic Fixture SCR-04-A", field(section, "Candidate Input"))
        self.assertEqual(field(section, "Required Behavior"), ORIGINAL_REQUIRED)
        self.assertEqual(field(section, "Prohibited Behavior"), ORIGINAL_PROHIBITED)
        self.assertEqual(field(section, "Pass Criteria"), ORIGINAL_PASS)

    def test_exactly_four_roles_have_complete_ranking_prerequisites(self):
        fixture = json.loads(DISCOVERY.read_text(encoding="utf-8"))
        self.assertTrue(fixture["profile_confirmed"])
        self.assertTrue(fixture["filters_confirmed"])
        self.assertEqual(fixture["requested_top_n"], 10)

        eligible = []
        for role in fixture["roles"]:
            self.assertTrue(role["canonical_job_id"])
            if role["verification_status"] == "jd_verified" and role["hard_gate_status"] == "PASS":
                self.assertTrue(role["canonical_url"].startswith("https://"))
                self.assertTrue(role["retrieved_at"])
                path = FIXTURE / role["jd_snapshot_path"]
                self.assertTrue(path.is_file(), role["canonical_job_id"])
                frontmatter = yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])
                self.assertEqual(frontmatter["canonical_job_id"], role["canonical_job_id"])
                self.assertEqual(frontmatter["canonical_url"], role["canonical_url"])
                self.assertEqual(frontmatter["retrieved_at"], role["retrieved_at"])
                eligible.append(role["canonical_job_id"])

        self.assertEqual(eligible, ["scr04-r1", "scr04-r2", "scr04-r3", "scr04-r4"])

    def test_failures_and_duplicate_cannot_enter_top_n(self):
        fixture = json.loads(DISCOVERY.read_text(encoding="utf-8"))
        roles = {role["role_id"]: role for role in fixture["roles"]}
        self.assertEqual(roles["R5"]["hard_gate_status"], "FAIL")
        self.assertEqual(roles["R6"]["hard_gate_status"], "FAIL")
        self.assertEqual(roles["R7"]["duplicate_of"], "scr04-r2")
        self.assertEqual(roles["R8"]["verification_status"], "verification_failed")
        self.assertEqual(
            [role["canonical_job_id"] for role in fixture["top_n"]],
            ["scr04-r1", "scr04-r2", "scr04-r3", "scr04-r4"],
        )
        self.assertEqual(fixture["shortfall"], 6)


if __name__ == "__main__":
    unittest.main()
