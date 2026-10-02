"""Keep Task 9 reference persistence claims within the Task 6 contract."""

import importlib.util
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_tracker_contract():
    path = ROOT / "scripts" / "update_tracker.py"
    spec = importlib.util.spec_from_file_location("task6_update_tracker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Task9PersistenceReferenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tracker = load_tracker_contract()

    def reference(self, name):
        return (ROOT / "references" / name).read_text(encoding="utf-8")

    def test_scheduled_run_is_not_declared_as_tracker_event_type(self):
        text = self.reference("privacy-and-consent.md")

        self.assertIsNone(
            re.search(
                r"(?:append|record|write)[^.\n]{0,120}`?scheduled_run`?[^.\n]{0,80}(?:event|event_type)",
                text,
                re.I,
            )
            or re.search(
                r"(?:event|event_type)[^.\n]{0,80}`?scheduled_run`?",
                text,
                re.I,
            )
        )
        self.assertNotIn("scheduled_run", self.tracker.EVENT_TYPES)
        self.assertRegex(text, r"tracker event vocabulary")

    def test_waiting_for_candidate_submit_is_not_a_tracker_identifier(self):
        text = self.reference("assisted-application.md")

        self.assertIsNone(
            re.search(
                r"(?:event|event_type|application_stage|action_status)[^.\n]{0,100}`?waiting_for_candidate_submit`?",
                text,
                re.I,
            )
            or re.search(
                r"`?waiting_for_candidate_submit`?[^.\n]{0,100}(?:event|event_type|application_stage|action_status)",
                text,
                re.I,
            )
        )
        all_identifiers = set(self.tracker.EVENT_TYPES)
        for values in self.tracker.STATUS_DIMENSIONS.values():
            all_identifiers.update(values)
        self.assertNotIn("waiting_for_candidate_submit", all_identifiers)
        self.assertRegex(text, r"workflow pause")


if __name__ == "__main__":
    unittest.main()
