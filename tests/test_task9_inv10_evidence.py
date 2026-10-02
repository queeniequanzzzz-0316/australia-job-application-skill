"""Fail-closed audit contract for Task 9 INV-10 behavioral evidence."""

import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_inv10_evidence.py"

STATUS_FIELDS = (
    "discovery_status",
    "verification_status",
    "screening_status",
    "application_stage",
    "action_status",
)


class Inv10EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.box = tempfile.TemporaryDirectory()
        self.addCleanup(self.box.cleanup)
        self.root = Path(self.box.name)
        (self.root / "tracker").mkdir()
        self.canonical_job_id = "synthetic-role-1"
        self.event_id = "event-synthetic-1"
        self.response = self.root / "response.json"
        self.state = self.root / "state.json"
        self.applications = self.root / "tracker" / "applications.csv"
        self.events = self.root / "tracker" / "events.jsonl"
        self._write_valid_fixture()

    @staticmethod
    def _digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _write_valid_fixture(self):
        statuses = {
            "discovery_status": "discovered",
            "verification_status": "jd_verified",
            "screening_status": "excluded",
            "application_stage": "not_started",
            "action_status": "blocked",
        }
        with self.applications.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=("canonical_job_id", *STATUS_FIELDS)
            )
            writer.writeheader()
            writer.writerow({"canonical_job_id": self.canonical_job_id, **statuses})
        event = {
            "event_id": self.event_id,
            "event_type": "status_changed",
            "canonical_job_id": self.canonical_job_id,
            "to_status": statuses,
            "reason": "Synthetic hard-gate exclusion.",
        }
        self.events.write_text(json.dumps(event) + "\n", encoding="utf-8")
        state = {
            "scenario_id": "SCR-02",
            "canonical_job_id": self.canonical_job_id,
            "tracker_path": "tracker/applications.csv",
            "event_log_path": "tracker/events.jsonl",
            "tracker_sha256": self._digest(self.applications),
            "event_log_sha256": self._digest(self.events),
            "event_id": self.event_id,
            "execution_trace": [
                {
                    "sequence": 1,
                    "operation": "event_appended",
                    "canonical_job_id": self.canonical_job_id,
                },
                {
                    "sequence": 2,
                    "operation": "current_state_published",
                    "canonical_job_id": self.canonical_job_id,
                },
                {
                    "sequence": 3,
                    "operation": "final_report_emitted",
                    "canonical_job_id": self.canonical_job_id,
                },
            ],
        }
        self.state.write_text(json.dumps(state), encoding="utf-8")
        response = {
            "scenario_id": "SCR-02",
            "supporting_state_artifact": "state.json",
            "complete_visible_transcript": {"final": "Excluded before Top N."},
        }
        self.response.write_text(json.dumps(response), encoding="utf-8")

    def _run(self):
        self.assertTrue(SCRIPT.is_file(), "INV-10 evaluator script is missing")
        process = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--response",
                str(self.response),
                "--state",
                str(self.state),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        try:
            report = json.loads(process.stdout)
        except json.JSONDecodeError as error:
            self.fail(f"evaluator did not return JSON: {error}: {process.stderr}")
        return process, report

    def _rewrite_state_hashes(self):
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["tracker_sha256"] = self._digest(self.applications)
        state["event_log_sha256"] = self._digest(self.events)
        self.state.write_text(json.dumps(state), encoding="utf-8")

    def test_valid_tracker_event_and_pre_report_order_pass(self):
        process, report = self._run()
        self.assertEqual(process.returncode, 0)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["canonical_job_id"], self.canonical_job_id)

    def test_missing_tracker_fails_closed(self):
        self.applications.unlink()
        process, report = self._run()
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(report["result"], "FAIL")
        self.assertIn("tracker_missing", {error["code"] for error in report["errors"]})

    def test_missing_event_fails_closed(self):
        self.events.write_text("", encoding="utf-8")
        self._rewrite_state_hashes()
        process, report = self._run()
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(report["result"], "FAIL")
        self.assertIn("event_missing", {error["code"] for error in report["errors"]})

    def test_wrong_canonical_job_id_fails_closed(self):
        event = json.loads(self.events.read_text(encoding="utf-8"))
        event["canonical_job_id"] = "different-role"
        self.events.write_text(json.dumps(event) + "\n", encoding="utf-8")
        self._rewrite_state_hashes()
        process, report = self._run()
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(report["result"], "FAIL")
        self.assertIn(
            "canonical_job_id_mismatch",
            {error["code"] for error in report["errors"]},
        )

    def test_persistence_after_final_report_fails_closed(self):
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["execution_trace"] = [
            {
                "sequence": 1,
                "operation": "final_report_emitted",
                "canonical_job_id": self.canonical_job_id,
            },
            {
                "sequence": 2,
                "operation": "event_appended",
                "canonical_job_id": self.canonical_job_id,
            },
            {
                "sequence": 3,
                "operation": "current_state_published",
                "canonical_job_id": self.canonical_job_id,
            },
        ]
        self.state.write_text(json.dumps(state), encoding="utf-8")
        process, report = self._run()
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(report["result"], "FAIL")
        self.assertIn(
            "persistence_after_report",
            {error["code"] for error in report["errors"]},
        )

    def test_missing_or_empty_scenario_and_event_ids_fail_closed(self):
        cases = (
            ("scenario_id", None, "scenario_id_missing"),
            ("scenario_id", "", "scenario_id_missing"),
            ("event_id", None, "event_id_missing"),
            ("event_id", "", "event_id_missing"),
        )
        for field, value, expected_code in cases:
            with self.subTest(field=field, value=value):
                self._write_valid_fixture()
                if field == "scenario_id":
                    response = json.loads(self.response.read_text(encoding="utf-8"))
                    state = json.loads(self.state.read_text(encoding="utf-8"))
                    if value is None:
                        response.pop(field)
                        state.pop(field)
                    else:
                        response[field] = value
                        state[field] = value
                    self.response.write_text(json.dumps(response), encoding="utf-8")
                    self.state.write_text(json.dumps(state), encoding="utf-8")
                else:
                    state = json.loads(self.state.read_text(encoding="utf-8"))
                    event = json.loads(self.events.read_text(encoding="utf-8"))
                    if value is None:
                        state.pop(field)
                        event.pop(field)
                    else:
                        state[field] = value
                        event[field] = value
                    self.events.write_text(json.dumps(event) + "\n", encoding="utf-8")
                    self.state.write_text(json.dumps(state), encoding="utf-8")
                    self._rewrite_state_hashes()

                process, report = self._run()
                self.assertNotEqual(process.returncode, 0)
                self.assertEqual(report["result"], "FAIL")
                self.assertIn(
                    expected_code,
                    {error["code"] for error in report["errors"]},
                )

    def test_status_outside_task6_vocabulary_fails_closed(self):
        statuses = {field: "bogus" for field in STATUS_FIELDS}
        with self.applications.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=("canonical_job_id", *STATUS_FIELDS)
            )
            writer.writeheader()
            writer.writerow({"canonical_job_id": self.canonical_job_id, **statuses})
        event = json.loads(self.events.read_text(encoding="utf-8"))
        event["to_status"] = statuses
        self.events.write_text(json.dumps(event) + "\n", encoding="utf-8")
        self._rewrite_state_hashes()

        process, report = self._run()
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(report["result"], "FAIL")
        self.assertIn(
            "status_value_invalid",
            {error["code"] for error in report["errors"]},
        )

    def test_malformed_supporting_state_link_fails_with_structured_report(self):
        response = json.loads(self.response.read_text(encoding="utf-8"))
        response["supporting_state_artifact"] = []
        self.response.write_text(json.dumps(response), encoding="utf-8")

        process, report = self._run()
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(report["result"], "FAIL")
        self.assertIn(
            "state_link_invalid",
            {error["code"] for error in report["errors"]},
        )
        self.assertNotIn("Traceback", process.stderr)

    def test_null_byte_in_supporting_state_link_fails_with_structured_report(self):
        response = json.loads(self.response.read_text(encoding="utf-8"))
        response["supporting_state_artifact"] = "bad\0path"
        self.response.write_text(json.dumps(response), encoding="utf-8")

        process, report = self._run()
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(report["result"], "FAIL")
        self.assertIn(
            "state_link_invalid",
            {error["code"] for error in report["errors"]},
        )
        self.assertNotIn("Traceback", process.stderr)

    def test_current_scr02_and_scr06_evidence_and_evaluators_are_complete(self):
        for scenario_id, directory in (("SCR-02", "scr-02"), ("SCR-06", "scr-06")):
            with self.subTest(scenario_id=scenario_id):
                response = (
                    ROOT / "evals" / "task9" / "current" / "responses"
                    / f"{scenario_id}.json"
                )
                state = (
                    ROOT / "evals" / "task9" / "current" / directory
                    / "persistence-state.json"
                )
                process = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "--response",
                        str(response),
                        "--state",
                        str(state),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                report = json.loads(process.stdout)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(report["result"], "PASS")
                self.assertEqual(report["errors"], [])

                evaluator_path = (
                    ROOT / "evals" / "task9" / "current" / "evaluations"
                    / f"{scenario_id}.json"
                )
                evaluator = json.loads(evaluator_path.read_text(encoding="utf-8"))
                self.assertEqual(evaluator["result"], "PASS")
                self.assertIn("INV-10", evaluator["relevant_invariants"])
                self.assertEqual(evaluator["evidence_evaluation"], report)


if __name__ == "__main__":
    unittest.main()
