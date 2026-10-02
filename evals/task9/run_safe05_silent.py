#!/usr/bin/env python3
"""Run the synthetic SAFE-05 no-action automation without visible output."""

import argparse
import json
from pathlib import Path


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    transcript = {
        "commentary": [],
        "progress": [],
        "status": [],
        "final": "",
        "other_visible": [],
    }
    state = {
        "fixture": "SAFE-05",
        "synthetic": True,
        "source_system": "optional_scheduled_automation",
        "persistence_boundary": "automation_audit_only",
        "outcome": "no_actionable_change",
        "notification_sent": False,
        "top_n_before": ["R1", "R2"],
        "top_n_after": ["R1", "R2"],
        "discovery_outcomes": [
            {
                "discovery_candidate_id": "SAFE05-D1",
                "outcome": "rejected",
                "reason": "duplicate_of_known",
            },
            {
                "discovery_candidate_id": "SAFE05-D2",
                "outcome": "rejected",
                "reason": "source_closed",
            },
            {
                "discovery_candidate_id": "SAFE05-D3",
                "outcome": "rejected",
                "reason": "hard_gate_failed",
            },
        ],
        "tracker_writes": [],
    }

    write_json(args.output_dir / "complete-visible-transcript.json", transcript)
    write_json(args.output_dir / "automation-run-state.json", state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
