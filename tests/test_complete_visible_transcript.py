"""Regression tests for complete user-visible transcript evaluation."""

import unittest


class CompleteVisibleTranscriptTests(unittest.TestCase):
    def evaluate(self, *, commentary=(), progress=(), status=(), final=""):
        from scripts.evaluate_visible_transcript import evaluate_visible_transcript

        return evaluate_visible_transcript(
            {
                "commentary": list(commentary),
                "progress": list(progress),
                "status": list(status),
                "final": final,
            }
        )

    def test_empty_final_with_commentary_is_not_silent(self):
        result = self.evaluate(commentary=("Checking the scheduled run.",))

        self.assertFalse(result["silent"])
        self.assertEqual(result["visible_message_count"], 1)
        self.assertEqual(result["nonempty_channels"], ["commentary"])

    def test_all_user_visible_channels_empty_is_silent(self):
        result = self.evaluate()

        self.assertTrue(result["silent"])
        self.assertEqual(result["visible_message_count"], 0)
        self.assertEqual(result["nonempty_channels"], [])

    def test_nonempty_final_is_not_silent(self):
        result = self.evaluate(final="No actionable changes.")

        self.assertFalse(result["silent"])
        self.assertEqual(result["visible_message_count"], 1)
        self.assertEqual(result["nonempty_channels"], ["final"])


if __name__ == "__main__":
    unittest.main()
