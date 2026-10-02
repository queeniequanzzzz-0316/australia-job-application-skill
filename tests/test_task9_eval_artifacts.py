"""Audit contract for the preserved Task 9 behavioral evidence."""

import json
from pathlib import Path
import unittest

from scripts.evaluate_visible_transcript import evaluate_visible_transcript


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "evals" / "task9"
FINAL = ARTIFACTS / "final-results.json"
FALSE_POSITIVE_FINAL = ARTIFACTS / "false-positive-final-results.json"
FALSE_POSITIVE_SAFE_05 = ARTIFACTS / "false-positive-safe-05-state.json"
HISTORY = ARTIFACTS / "failure-history.md"

EXPECTED_IDS = [
    *(f"ONB-{number:02d}" for number in range(1, 6)),
    *(f"SCR-{number:02d}" for number in range(1, 8)),
    *(f"SAFE-{number:02d}" for number in range(1, 7)),
]


class Task9EvalArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(FINAL.read_text(encoding="utf-8"))

    def test_final_report_contains_the_original_eighteen_scenarios_once(self):
        scenarios = self.report["scenarios"]
        ids = [scenario["scenario_id"] for scenario in scenarios]
        self.assertEqual(ids, EXPECTED_IDS)
        self.assertEqual(len(set(ids)), 18)
        self.assertEqual(self.report["totals"], {
            "ONB": {"passed": 5, "total": 5},
            "SCR": {"passed": 7, "total": 7},
            "SAFE": {"passed": 6, "total": 6},
            "overall": {"passed": 18, "failed": 0, "total": 18},
        })

    def test_each_scenario_preserves_independent_auditable_evidence(self):
        responder_threads = set()
        for scenario in self.report["scenarios"]:
            self.assertEqual(scenario["result"], "PASS", scenario["scenario_id"])
            self.assertTrue(scenario["input_source"])
            self.assertTrue(scenario["responder_model"])
            self.assertTrue(scenario["responder_thread_id"])
            self.assertNotIn(scenario["responder_thread_id"], responder_threads)
            responder_threads.add(scenario["responder_thread_id"])
            self.assertIn("output", scenario)
            transcript = scenario["complete_visible_transcript"]
            evaluation = evaluate_visible_transcript(transcript)
            self.assertEqual(scenario["visible_transcript_evaluation"], evaluation)
            self.assertEqual(transcript["final"], scenario["output"])
            if scenario["scenario_id"] == "SAFE-05":
                self.assertEqual(scenario["output"], "")
                self.assertTrue(evaluation["silent"])
                self.assertEqual(evaluation["visible_message_count"], 0)
                self.assertTrue(scenario["supporting_state_artifact"])
            else:
                self.assertTrue(scenario["output"].strip())
                self.assertFalse(evaluation["silent"])
            self.assertTrue(scenario["evaluator_thread_id"])
            self.assertTrue(scenario["reason"].strip())
            self.assertTrue(scenario["relevant_invariants"])
            self.assertEqual(scenario["missing_or_violated"], [])

    def test_report_records_method_without_hardcoding_evaluator_passes(self):
        methodology = self.report["methodology"]
        self.assertEqual(methodology["scenario_source"], "Task 1 original 18 scenarios")
        self.assertTrue(methodology["fresh_responder_per_scenario"])
        self.assertTrue(methodology["independent_evaluator"])
        self.assertTrue(methodology["complete_visible_transcript_evaluated"])
        self.assertFalse(methodology["criteria_changed"])
        self.assertFalse(methodology["evaluator_weakened"])
        self.assertEqual(
            self.report["totals"]["overall"]["passed"],
            sum(scenario["result"] == "PASS" for scenario in self.report["scenarios"]),
        )

    def test_scr02_and_scr06_include_audited_inv10_evidence(self):
        scenarios = {
            scenario["scenario_id"]: scenario for scenario in self.report["scenarios"]
        }
        self.assertEqual(
            self.report["methodology"]["inv10_evidence_evaluated_for"],
            ["SCR-02", "SCR-06"],
        )
        for scenario_id in ("SCR-02", "SCR-06"):
            with self.subTest(scenario_id=scenario_id):
                scenario = scenarios[scenario_id]
                self.assertIn("INV-10", scenario["relevant_invariants"])
                self.assertTrue(scenario["supporting_state_artifact"])
                self.assertEqual(scenario["evidence_evaluation"]["result"], "PASS")
                self.assertEqual(scenario["evidence_evaluation"]["errors"], [])

    def test_false_positive_artifacts_are_preserved_as_history(self):
        self.assertTrue(FALSE_POSITIVE_FINAL.is_file())
        self.assertTrue(FALSE_POSITIVE_SAFE_05.is_file())
        historical = json.loads(FALSE_POSITIVE_FINAL.read_text(encoding="utf-8"))
        self.assertEqual(historical["artifact_status"], "historical_false_positive")
        self.assertIn("SAFE-05", historical["invalidated_by"])
        safe_05 = json.loads(FALSE_POSITIVE_SAFE_05.read_text(encoding="utf-8"))
        self.assertEqual(safe_05["artifact_status"], "historical_false_positive")

    def test_failure_history_preserves_baseline_and_intermediate_failures(self):
        text = HISTORY.read_text(encoding="utf-8")
        self.assertIn("Task 1 baseline: 9/18 PASS", text)
        self.assertIn("Task 9 first rerun: 12/18 PASS", text)
        self.assertIn("SCR-05", text)
        self.assertIn("fixture", text.casefold())
        self.assertIn("Pass Criteria unchanged", text)
        self.assertIn("Evaluator unchanged", text)


if __name__ == "__main__":
    unittest.main()
