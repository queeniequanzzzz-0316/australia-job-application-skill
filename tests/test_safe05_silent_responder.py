"""Isolated behavioral contract for a no-action scheduled run."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.evaluate_visible_transcript import evaluate_visible_transcript


ROOT = Path(__file__).resolve().parents[1]
RESPONDER = ROOT / "evals" / "task9" / "run_safe05_silent.py"


class Safe05SilentResponderTests(unittest.TestCase):
    def test_no_action_run_has_no_user_visible_output_or_tracker_write(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            completed = subprocess.run(
                [sys.executable, str(RESPONDER), "--output-dir", str(output)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")

            transcript = json.loads(
                (output / "complete-visible-transcript.json").read_text(encoding="utf-8")
            )
            self.assertTrue(evaluate_visible_transcript(transcript)["silent"])

            state = json.loads(
                (output / "automation-run-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["top_n_before"], state["top_n_after"])
            self.assertEqual(state["outcome"], "no_actionable_change")
            self.assertFalse(state["notification_sent"])
            self.assertEqual(state["persistence_boundary"], "automation_audit_only")
            self.assertFalse((output / "tracker" / "applications.csv").exists())
            self.assertFalse((output / "tracker" / "events.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
