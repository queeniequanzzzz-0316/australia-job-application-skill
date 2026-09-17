import csv
import os
import pathlib
import subprocess
import tempfile
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "initialise_workspace.py"
TEMPLATE_ROOT = ROOT / "assets" / "workspace-template"

EXPECTED_DIRECTORIES = {
    "profile",
    "filters",
    "jobs",
    "jobs/snapshots",
    "resumes",
    "cover-letters",
    "tracker",
    "reports",
    "dashboard",
}

EXPECTED_FILES = {
    "profile/candidate-profile.yaml",
    "profile/analytics-consent.yaml",
    "filters/search-filter.yaml",
    "tracker/applications.csv",
    "tracker/events.jsonl",
}

APPLICATION_HEADER = [
    "canonical_job_id",
    "jd_snapshot_path",
    "source",
    "canonical_url",
    "job_title",
    "company",
    "location",
    "discovery_status",
    "verification_status",
    "screening_status",
    "application_stage",
    "action_status",
    "first_seen_at",
    "last_verified_at",
    "applied_at",
    "interview_at",
    "offer_at",
    "first_response_at",
    "next_action",
    "action_due_at",
    "blocker_category",
]


class InitialiseWorkspaceTests(unittest.TestCase):
    def run_initializer(self, *arguments, cwd=None):
        return subprocess.run(
            ["python3", str(SCRIPT), *map(str, arguments)],
            cwd=cwd or ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def initialise(self, destination):
        result = self.run_initializer("--destination", destination)
        self.assertEqual(result.returncode, 0, result.stderr)

    def snapshot_tree(self, root):
        return {
            str(path.relative_to(root)): (
                "directory" if path.is_dir() else "file",
                path.read_bytes() if path.is_file() else None,
            )
            for path in root.rglob("*")
        }

    def assert_generic_unsafe_path_error(self, result, private_path):
        self.assertEqual(result.returncode, 2)
        self.assertIn("error: unsafe workspace path", result.stderr.lower())
        self.assertNotIn("traceback", result.stderr.lower())
        self.assertNotIn(str(private_path), result.stderr)

    def assert_unsafe_workspace_is_rejected(self, destination, outside_directory):
        workspace_before = sorted(
            str(path.relative_to(destination)) for path in destination.rglob("*")
        )

        result = self.run_initializer("--destination", destination)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe workspace path", result.stderr.lower())
        self.assertNotIn(str(outside_directory.resolve()), result.stderr)
        self.assertEqual(list(outside_directory.rglob("*")), [])
        self.assertEqual(
            sorted(str(path.relative_to(destination)) for path in destination.rglob("*")),
            workspace_before,
        )

    def test_creates_exact_workspace_structure_and_defaults(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = pathlib.Path(temporary_directory) / "private-workspace"

            self.initialise(destination)

            actual_directories = {
                str(path.relative_to(destination))
                for path in destination.rglob("*")
                if path.is_dir()
            }
            actual_files = {
                str(path.relative_to(destination))
                for path in destination.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual_directories, EXPECTED_DIRECTORIES)
            self.assertEqual(actual_files, EXPECTED_FILES)

            candidate_profile = yaml.safe_load(
                (destination / "profile" / "candidate-profile.yaml").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(candidate_profile["market"], "Australia")
            self.assertEqual(candidate_profile["candidate_location_mode"], "onshore")
            self.assertEqual(candidate_profile["employment_streams"], ["professional"])
            self.assertEqual(candidate_profile["target_roles"], [])
            self.assertEqual(candidate_profile["target_locations"], [])
            self.assertEqual(
                candidate_profile["work_rights"],
                {
                    "status": "unknown",
                    "restrictions": "unknown",
                    "sponsorship_required": "unknown",
                },
            )
            self.assertEqual(
                candidate_profile["salary"],
                {"basis": "annual", "target_aud": None},
            )
            self.assertEqual(candidate_profile["evidence_inventory"], [])

            search_filter = yaml.safe_load(
                (destination / "filters" / "search-filter.yaml").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(search_filter["top_n"], 20)
            self.assertEqual(search_filter["sources"], ["SEEK", "LinkedIn", "Indeed"])
            self.assertEqual(search_filter["locations"], [])
            self.assertEqual(search_filter["employment_types"], [])
            self.assertEqual(
                search_filter["salary_bounds"],
                {"minimum_aud": None, "maximum_aud": None},
            )
            self.assertEqual(
                search_filter["rate_bounds"],
                {"minimum_aud_per_hour": None, "maximum_aud_per_hour": None},
            )
            self.assertEqual(search_filter["remote_preference"], "unknown")
            self.assertEqual(search_filter["sponsorship_need"], "unknown")
            self.assertIsNone(search_filter["recency_days"])
            self.assertEqual(search_filter["exclusions"], [])
            self.assertEqual(search_filter["hard_gates"], [])
            self.assertIs(search_filter["confirmed"], False)

            analytics_consent = yaml.safe_load(
                (destination / "profile" / "analytics-consent.yaml").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                analytics_consent,
                {
                    "scheduled_workflows": {
                        "morning_discovery": False,
                        "evening_check_in": False,
                    },
                    "cross_user_analytics": {
                        "enabled": False,
                        "share_salary_band": False,
                        "share_broad_work_rights": False,
                    },
                },
            )

            with (destination / "tracker" / "applications.csv").open(
                newline="", encoding="utf-8"
            ) as applications_file:
                self.assertEqual(next(csv.reader(applications_file)), APPLICATION_HEADER)
                self.assertEqual(list(csv.reader(applications_file)), [])

            self.assertEqual(
                (destination / "tracker" / "events.jsonl").read_bytes(), b""
            )

    def test_rerun_without_force_preserves_candidate_profile(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = pathlib.Path(temporary_directory) / "private-workspace"
            self.initialise(destination)
            candidate_path = destination / "profile" / "candidate-profile.yaml"
            private_candidate_profile = "candidate_name: Private Candidate\n"
            candidate_path.write_text(private_candidate_profile, encoding="utf-8")

            self.initialise(destination)

            self.assertEqual(
                candidate_path.read_text(encoding="utf-8"), private_candidate_profile
            )

    def test_force_restores_only_candidate_profile_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = pathlib.Path(temporary_directory) / "private-workspace"
            self.initialise(destination)

            candidate_path = destination / "profile" / "candidate-profile.yaml"
            search_filter_path = destination / "filters" / "search-filter.yaml"
            consent_path = destination / "profile" / "analytics-consent.yaml"
            applications_path = destination / "tracker" / "applications.csv"
            events_path = destination / "tracker" / "events.jsonl"

            candidate_path.write_text("candidate_name: Private Candidate\n", encoding="utf-8")
            search_filter_contents = "confirmed: true\n"
            consent_contents = "scheduled_workflows:\n  morning_discovery: true\n"
            applications_contents = (
                ",".join(APPLICATION_HEADER)
                + "\njob-1,jobs/snapshots/job-1.yaml,SEEK,https://example.invalid/job-1,"
                "Example Role,Example Company,Melbourne,discovered,verified,draft,"
                "2026-09-10T00:00:00Z,,,,\n"
            )
            events_contents = '{"event": "example"}\n'
            search_filter_path.write_text(search_filter_contents, encoding="utf-8")
            consent_path.write_text(consent_contents, encoding="utf-8")
            applications_path.write_text(applications_contents, encoding="utf-8")
            events_path.write_text(events_contents, encoding="utf-8")

            result = self.run_initializer("--destination", destination, "--force")
            self.assertEqual(result.returncode, 0, result.stderr)

            self.assertEqual(
                candidate_path.read_bytes(),
                (TEMPLATE_ROOT / "candidate-profile.example.yaml").read_bytes(),
            )
            self.assertEqual(
                search_filter_path.read_text(encoding="utf-8"), search_filter_contents
            )
            self.assertEqual(consent_path.read_text(encoding="utf-8"), consent_contents)
            self.assertEqual(
                applications_path.read_text(encoding="utf-8"), applications_contents
            )
            self.assertEqual(events_path.read_text(encoding="utf-8"), events_contents)

    def test_destination_is_required_and_no_public_default_is_created(self):
        public_default = ROOT / "job-search-workspace"
        self.assertFalse(public_default.exists())

        result = self.run_initializer(cwd=ROOT)

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(public_default.exists())

    def test_destination_inside_public_repository_is_rejected(self):
        with tempfile.TemporaryDirectory(
            dir=ROOT, prefix=".task3-boundary-test-"
        ) as temporary_directory:
            destination = pathlib.Path(temporary_directory) / "private-workspace"

            result = self.run_initializer("--destination", destination)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(destination.exists())

    def test_rejects_profile_symlink_without_any_filesystem_mutation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            destination = root / "private-workspace"
            outside_directory = root / "outside"
            destination.mkdir()
            outside_directory.mkdir()
            (destination / "profile").symlink_to(
                outside_directory, target_is_directory=True
            )

            self.assert_unsafe_workspace_is_rejected(destination, outside_directory)

    def test_rejects_filters_symlink_without_any_filesystem_mutation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            destination = root / "private-workspace"
            outside_directory = root / "outside"
            destination.mkdir()
            outside_directory.mkdir()
            (destination / "filters").symlink_to(
                outside_directory, target_is_directory=True
            )

            self.assert_unsafe_workspace_is_rejected(destination, outside_directory)

    def test_rejects_tracker_symlink_without_any_filesystem_mutation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            destination = root / "private-workspace"
            outside_directory = root / "outside"
            destination.mkdir()
            outside_directory.mkdir()
            (destination / "tracker").symlink_to(
                outside_directory, target_is_directory=True
            )

            self.assert_unsafe_workspace_is_rejected(destination, outside_directory)

    def test_rejects_jobs_symlink_without_any_filesystem_mutation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            destination = root / "private-workspace"
            outside_directory = root / "outside"
            destination.mkdir()
            outside_directory.mkdir()
            (destination / "jobs").symlink_to(
                outside_directory, target_is_directory=True
            )

            self.assert_unsafe_workspace_is_rejected(destination, outside_directory)

    def test_rejects_snapshots_symlink_without_any_filesystem_mutation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            destination = root / "private-workspace"
            outside_directory = root / "outside"
            (destination / "jobs").mkdir(parents=True)
            outside_directory.mkdir()
            (destination / "jobs" / "snapshots").symlink_to(
                outside_directory, target_is_directory=True
            )

            self.assert_unsafe_workspace_is_rejected(destination, outside_directory)

    def test_rejects_destination_symlink_without_any_filesystem_mutation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            destination = root / "private-workspace"
            outside_directory = root / "outside"
            outside_directory.mkdir()
            destination.symlink_to(outside_directory, target_is_directory=True)

            self.assert_unsafe_workspace_is_rejected(destination, outside_directory)

    def test_force_rejects_hard_link_without_modifying_outside_file_or_workspace(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            destination = root / "private-workspace"
            outside_directory = root / "outside"
            outside_sentinel = outside_directory / "candidate-profile.yaml"
            self.initialise(destination)
            outside_directory.mkdir()
            outside_contents = b"outside sentinel\n"
            outside_sentinel.write_bytes(outside_contents)

            candidate_path = destination / "profile" / "candidate-profile.yaml"
            candidate_path.unlink()
            os.link(outside_sentinel, candidate_path)
            workspace_before = {
                str(path.relative_to(destination)): (
                    path.read_bytes() if path.is_file() else None
                )
                for path in destination.rglob("*")
            }

            result = self.run_initializer("--destination", destination, "--force")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe workspace path", result.stderr.lower())
            self.assertNotIn(str(outside_sentinel.resolve()), result.stderr)
            self.assertEqual(outside_sentinel.read_bytes(), outside_contents)
            self.assertEqual(
                {
                    str(path.relative_to(destination)): (
                        path.read_bytes() if path.is_file() else None
                    )
                    for path in destination.rglob("*")
                },
                workspace_before,
            )

    def test_rejects_missing_destination_parent_without_creating_it(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            missing_parent = root / "missing-parent"
            destination = missing_parent / "private-workspace"
            self.assertFalse(missing_parent.exists())

            result = self.run_initializer("--destination", destination)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe workspace path", result.stderr.lower())
            self.assertNotIn(str(missing_parent), result.stderr)
            self.assertFalse(missing_parent.exists())

    def test_filesystem_validation_error_uses_generic_private_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            private_component = "private-" + ("x" * 300)
            destination = root / private_component
            self.assertEqual(list(root.iterdir()), [])

            result = self.run_initializer("--destination", destination)

            self.assertEqual(result.returncode, 2)
            self.assertIn("error: unsafe workspace path", result.stderr.lower())
            self.assertNotIn("traceback", result.stderr.lower())
            self.assertNotIn(private_component, result.stderr)
            self.assertEqual(list(root.iterdir()), [])

    def test_rejects_workspace_directory_path_that_is_a_file_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            destination = root / "private-workspace"
            destination.mkdir()
            (destination / "filters").write_bytes(b"not a directory\n")
            tree_before = self.snapshot_tree(root)

            result = self.run_initializer("--destination", destination)

            self.assert_generic_unsafe_path_error(result, destination)
            self.assertEqual(self.snapshot_tree(root), tree_before)

    def test_rejects_workspace_file_path_that_is_a_directory_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            destination = root / "private-workspace"
            (destination / "tracker" / "events.jsonl").mkdir(parents=True)
            tree_before = self.snapshot_tree(root)

            result = self.run_initializer("--destination", destination)

            self.assert_generic_unsafe_path_error(result, destination)
            self.assertEqual(self.snapshot_tree(root), tree_before)

    def test_rejects_destination_that_is_a_file_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            destination = root / "private-workspace"
            destination.write_bytes(b"not a directory\n")
            tree_before = self.snapshot_tree(root)

            result = self.run_initializer("--destination", destination)

            self.assert_generic_unsafe_path_error(result, destination)
            self.assertEqual(self.snapshot_tree(root), tree_before)

    def test_mutation_permission_error_uses_generic_private_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            destination_parent = root / "private-parent"
            destination = destination_parent / "private-workspace"
            permission_probe = destination_parent / "permission-probe"
            destination_parent.mkdir()
            destination_parent.chmod(0o500)

            try:
                try:
                    permission_probe.mkdir()
                except PermissionError:
                    pass
                else:
                    permission_probe.rmdir()
                    self.skipTest("filesystem does not enforce directory write denial")

                tree_before = self.snapshot_tree(root)

                result = self.run_initializer("--destination", destination)

                self.assert_generic_unsafe_path_error(result, destination)
                self.assertFalse(destination.exists())
                self.assertEqual(self.snapshot_tree(root), tree_before)
            finally:
                destination_parent.chmod(0o700)

    def test_public_templates_contain_no_candidate_or_contact_data(self):
        yaml_templates = (
            TEMPLATE_ROOT / "candidate-profile.example.yaml",
            TEMPLATE_ROOT / "search-filter.example.yaml",
            TEMPLATE_ROOT / "analytics-consent.example.yaml",
        )
        allowed_public_values = {
            None,
            False,
            20,
            "Australia",
            "onshore",
            "professional",
            "unknown",
            "annual",
            "SEEK",
            "LinkedIn",
            "Indeed",
        }

        def assert_safe_values(value, path):
            if isinstance(value, dict):
                for key, nested_value in value.items():
                    assert_safe_values(nested_value, f"{path}.{key}")
                return
            if isinstance(value, list):
                for index, nested_value in enumerate(value):
                    assert_safe_values(nested_value, f"{path}[{index}]")
                return
            self.assertIn(value, allowed_public_values, f"unsafe value at {path}")

        for template_path in yaml_templates:
            with self.subTest(template=template_path.name):
                contents = yaml.safe_load(template_path.read_text(encoding="utf-8"))
                assert_safe_values(contents, template_path.name)

        applications_template = TEMPLATE_ROOT / "applications.example.csv"
        with applications_template.open(newline="", encoding="utf-8") as csv_file:
            self.assertEqual(list(csv.reader(csv_file)), [APPLICATION_HEADER])


if __name__ == "__main__":
    unittest.main()
