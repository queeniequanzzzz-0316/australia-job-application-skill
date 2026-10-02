"""Contract tests for status values in active behavioral fixtures."""

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "fixtures"


def load_tracker_contract():
    path = ROOT / "scripts" / "update_tracker.py"
    spec = importlib.util.spec_from_file_location("fixture_update_tracker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_named_values(value, field_name, location="$"):
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key == field_name:
                yield child_location, child
            yield from find_named_values(child, field_name, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from find_named_values(child, field_name, f"{location}[{index}]")


class BehavioralFixtureVocabularyTests(unittest.TestCase):
    def test_active_verification_status_values_match_task6_contract(self):
        tracker = load_tracker_contract()
        approved = set(tracker.STATUS_DIMENSIONS["verification_status"])
        violations = []

        for path in sorted(FIXTURES.rglob("*.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            for location, value in find_named_values(document, "verification_status"):
                if value not in approved:
                    violations.append(f"{path.relative_to(ROOT)}:{location}={value!r}")

        self.assertEqual(violations, [], "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
