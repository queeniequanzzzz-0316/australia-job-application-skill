"""Behavioral fixture contract for SCR-05 duplicate-ad consolidation."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from urllib.parse import urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "evals" / "screening-cases.md"
FIXTURE = ROOT / "evals" / "fixtures" / "scr-05-workspace"
PROFILE = FIXTURE / "profile" / "candidate-profile.yaml"
FILTERS = FIXTURE / "filters" / "search-filter.yaml"
DISCOVERY = FIXTURE / "jobs" / "discovery-records.json"
JOB_RECORD = FIXTURE / "jobs" / "job-record.json"
CANONICAL_JOB_ID = "employer-delta-oa-205"
SNAPSHOT_PATH = f"jobs/snapshots/{CANONICAL_JOB_ID}.md"
SNAPSHOT = FIXTURE / SNAPSHOT_PATH
INITIALISER = ROOT / "scripts" / "initialise_workspace.py"
PROFILE_VALIDATOR = ROOT / "scripts" / "validate_profile.py"
SNAPSHOT_SAVER = ROOT / "scripts" / "save_job_snapshot.py"

ORIGINAL_REQUIRED = (
    "Deduplicate the vacancy, prefer the authoritative employer source, and "
    "retain alternate URLs as provenance."
)
ORIGINAL_PROHIBITED = "Do not count or rank the copies as separate opportunities."
ORIGINAL_PASS = (
    "One canonical role reaches ranking and its record links the duplicate sources."
)
SUPPORTED_SOURCES = {"employer_careers", "SEEK", "LinkedIn"}


def run_script(script, *arguments):
    return subprocess.run(
        ["python3", str(script), *map(str, arguments)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def scr05_section():
    text = SCENARIOS.read_text(encoding="utf-8")
    return text.split("## SCR-05: Duplicate ad\n", 1)[1].split("\n## SCR-06:", 1)[0]


def field(section, label):
    prefix = f"- **{label}:** "
    matches = [line[len(prefix):] for line in section.splitlines() if line.startswith(prefix)]
    if len(matches) != 1:
        raise AssertionError(f"SCR-05 must contain exactly one {label} field")
    return matches[0]


class Scr05BehavioralFixtureTests(unittest.TestCase):
    maxDiff = None

    def test_scenario_contract_is_unchanged_and_points_to_named_fixture(self):
        section = scr05_section()
        self.assertIn("Synthetic Fixture SCR-05-A", field(section, "Candidate Input"))
        self.assertEqual(field(section, "Required Behavior"), ORIGINAL_REQUIRED)
        self.assertEqual(field(section, "Prohibited Behavior"), ORIGINAL_PROHIBITED)
        self.assertEqual(field(section, "Pass Criteria"), ORIGINAL_PASS)

    def test_confirmed_candidate_profile_is_valid_and_not_mutated(self):
        before = PROFILE.read_bytes()
        document = yaml.safe_load(before)
        original = copy.deepcopy(document)

        result = run_script(PROFILE_VALIDATOR, "--profile", PROFILE)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["errors"], [])
        self.assertEqual(document, original)
        self.assertEqual(PROFILE.read_bytes(), before)
        self.assertEqual(document["candidate_location_mode"], "onshore")
        self.assertIn("Melbourne VIC", document["target_locations"])
        self.assertEqual(document["work_rights"]["status"], "unrestricted")
        self.assertFalse(document["work_rights"]["sponsorship_required"])
        self.assertIn("Operations Analyst", document["target_roles"])
        self.assertIn("professional", document["employment_streams"])
        claims = " ".join(item["claim"] for item in document["evidence_inventory"])
        self.assertIn("operations reporting", claims.casefold())
        self.assertIn("microsoft excel", claims.casefold())

    def test_search_filter_is_confirmed_and_matches_candidate_constraints(self):
        search_filter = yaml.safe_load(FILTERS.read_text(encoding="utf-8"))
        self.assertTrue(search_filter["confirmed"])
        self.assertEqual(search_filter["market"], "Australia")
        self.assertEqual(search_filter["timezone"], "Australia/Melbourne")
        self.assertEqual(search_filter["employment_stream"], "professional")
        self.assertEqual(search_filter["target_roles"], ["Operations Analyst"])
        self.assertEqual(search_filter["locations"], ["Melbourne VIC"])
        self.assertEqual(search_filter["employment_types"], ["full-time"])
        self.assertIsInstance(search_filter["top_n"], int)
        self.assertGreater(search_filter["top_n"], 0)
        self.assertEqual(search_filter["sponsorship_need"], False)
        gates = set(search_filter["hard_gates"])
        self.assertTrue(
            {
                "current_open_vacancy",
                "legitimate_employer",
                "melbourne_location",
                "full_time_employment",
                "work_rights_compatible",
                "mandatory_criteria_evidenced",
            }.issubset(gates)
        )

    def test_three_sources_collapse_to_one_canonical_ranking_candidate(self):
        fixture = json.loads(DISCOVERY.read_text(encoding="utf-8"))
        self.assertEqual(fixture["fixture_id"], "SCR-05-A")
        self.assertTrue(fixture["profile_confirmed"])
        self.assertTrue(fixture["filters_confirmed"])

        records = fixture["discovery_records"]
        self.assertEqual(len(records), 3)
        self.assertEqual({record["source"] for record in records}, SUPPORTED_SOURCES)
        self.assertEqual(
            {record["canonical_job_id"] for record in records},
            {CANONICAL_JOB_ID},
        )
        source_urls = {record["source_url"] for record in records}
        self.assertEqual(len(source_urls), 3)
        for url in source_urls:
            parts = urlsplit(url)
            self.assertEqual(parts.scheme, "https")
            self.assertTrue(parts.hostname)

        canonical_jobs = fixture["canonical_jobs"]
        self.assertEqual(len(canonical_jobs), 1)
        canonical = canonical_jobs[0]
        self.assertEqual(canonical["canonical_job_id"], CANONICAL_JOB_ID)
        self.assertEqual(canonical["primary_source"], "employer_careers")
        self.assertEqual(canonical["canonical_url"], records[0]["source_url"])
        self.assertEqual(set(canonical["alternate_urls"]), source_urls - {canonical["canonical_url"]})

        ranked_ids = [
            job["canonical_job_id"]
            for job in canonical_jobs
            if job["ranking_status"] == "eligible"
        ]
        self.assertEqual(ranked_ids, [CANONICAL_JOB_ID])

    def test_production_scripts_recreate_the_exact_checked_in_snapshot(self):
        expected = SNAPSHOT.read_bytes()
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory) / "workspace"
            initialised = run_script(INITIALISER, "--destination", workspace)
            self.assertEqual(initialised.returncode, 0, initialised.stderr)

            saved = run_script(
                SNAPSHOT_SAVER,
                "--workspace",
                workspace,
                "--input-json",
                JOB_RECORD,
            )
            self.assertEqual(saved.returncode, 0, saved.stdout + saved.stderr)
            report = json.loads(saved.stdout)
            self.assertEqual(report["status"], "created")
            self.assertEqual(report["canonical_job_id"], CANONICAL_JOB_ID)
            self.assertEqual(report["jd_snapshot_path"], SNAPSHOT_PATH)
            generated = workspace / report["jd_snapshot_path"]
            self.assertTrue(generated.is_file())
            self.assertEqual(generated.read_bytes(), expected)

        text = expected.decode("utf-8")
        frontmatter_text, body = text[4:].split("\n---\n", 1)
        frontmatter = yaml.safe_load(frontmatter_text)
        job = json.loads(JOB_RECORD.read_text(encoding="utf-8"))
        self.assertEqual(frontmatter["canonical_job_id"], CANONICAL_JOB_ID)
        self.assertEqual(frontmatter["canonical_url"], job["canonical_url"])
        self.assertEqual(frontmatter["retrieved_at"], job["retrieved_at"])
        self.assertEqual(frontmatter["last_verified_at"], job["last_verified_at"])
        self.assertEqual(body, job["job_description"])

        discovery = json.loads(DISCOVERY.read_text(encoding="utf-8"))
        canonical = discovery["canonical_jobs"][0]
        self.assertEqual(canonical["jd_snapshot_path"], SNAPSHOT_PATH)
        self.assertEqual(canonical["canonical_url"], frontmatter["canonical_url"])
        self.assertEqual(canonical["retrieved_at"], frontmatter["retrieved_at"])

    def test_fixture_facts_pass_verification_and_hard_gate_before_ranking(self):
        fixture = json.loads(DISCOVERY.read_text(encoding="utf-8"))
        canonical = fixture["canonical_jobs"][0]
        facts = canonical["gate_facts"]
        profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
        evidence_ids = {item["id"] for item in profile["evidence_inventory"]}

        verification_checks = {
            "live_open": facts["live_open"],
            "legitimate_employer": facts["legitimate_employer"],
            "location_verified": facts["location"] == "Melbourne VIC",
            "employment_type_verified": facts["employment_type"] == "full-time",
            "source_provenance_complete": bool(canonical["canonical_url"])
            and bool(canonical["retrieved_at"])
            and canonical["jd_snapshot_path"] == SNAPSHOT_PATH,
        }
        self.assertTrue(all(verification_checks.values()), verification_checks)

        criteria = facts["mandatory_criteria"]
        hard_gate_checks = {
            "work_rights_compatible": facts["accepts_unrestricted_work_rights"]
            and profile["work_rights"]["status"] == "unrestricted",
            "sponsorship_not_blocking": not facts["sponsorship_blocks_candidate"],
            "mandatory_criteria_evidenced": all(
                criterion["met"] and set(criterion["evidence_ids"]) <= evidence_ids
                for criterion in criteria
            ),
            "no_hidden_mandatory_criteria": not facts["hidden_mandatory_criteria"],
        }
        self.assertTrue(all(hard_gate_checks.values()), hard_gate_checks)
        self.assertEqual(canonical["verification_status"], "jd_verified")
        self.assertEqual(canonical["hard_gate_status"], "PASS")
        self.assertEqual(canonical["ranking_status"], "eligible")
        self.assertEqual(
            [
                job["canonical_job_id"]
                for job in fixture["canonical_jobs"]
                if job["verification_status"] == "jd_verified"
                and job["hard_gate_status"] == "PASS"
                and job["ranking_status"] == "eligible"
            ],
            [CANONICAL_JOB_ID],
        )


if __name__ == "__main__":
    unittest.main()
