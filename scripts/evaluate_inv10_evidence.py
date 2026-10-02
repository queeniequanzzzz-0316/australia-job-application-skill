#!/usr/bin/env python3
"""Fail-closed evaluator for Task 9 current-state and event evidence."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

from update_tracker import STATUS_DIMENSIONS


STATUS_FIELDS = (
    "discovery_status",
    "verification_status",
    "screening_status",
    "application_stage",
    "action_status",
)
REQUIRED_OPERATIONS = (
    "event_appended",
    "current_state_published",
    "final_report_emitted",
)


def error(code, message):
    return {"code": code, "message": message}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path, errors, code):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.append(error(code, "The required JSON artifact is unavailable or invalid."))
        return None
    if not isinstance(value, dict):
        errors.append(error(code, "The required JSON artifact is not an object."))
        return None
    return value


def contained_file(base, relative, errors, code):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        errors.append(error(code, "The evidence path must be a relative file path."))
        return None
    base = base.resolve()
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        errors.append(error(code, "The evidence path leaves its artifact directory."))
        return None
    if not candidate.is_file() or candidate.is_symlink():
        errors.append(error(code, "The required evidence file is missing."))
        return None
    return candidate


def read_tracker(path, canonical_job_id, errors):
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error):
        errors.append(error("tracker_invalid", "The tracker evidence is unreadable."))
        return None
    matches = [row for row in rows if row.get("canonical_job_id") == canonical_job_id]
    if len(matches) != 1:
        errors.append(
            error(
                "tracker_record_missing",
                "The tracker must contain exactly one row for the canonical job.",
            )
        )
        return None
    row = matches[0]
    if any(not row.get(field) for field in STATUS_FIELDS):
        errors.append(
            error("tracker_status_missing", "The tracker row lacks a status dimension.")
        )
    validate_statuses(row, errors)
    return row


def read_event(path, event_id, canonical_job_id, errors):
    records = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError
                records.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        errors.append(error("event_log_invalid", "The event log evidence is invalid."))
        return None
    matches = [record for record in records if record.get("event_id") == event_id]
    if len(matches) != 1:
        errors.append(
            error("event_missing", "The append-only log lacks the referenced event.")
        )
        return None
    event_record = matches[0]
    if event_record.get("canonical_job_id") != canonical_job_id:
        errors.append(
            error(
                "canonical_job_id_mismatch",
                "The tracker and event do not identify the same canonical job.",
            )
        )
    if event_record.get("event_type") != "status_changed":
        errors.append(
            error("event_type_invalid", "The evidence is not a status_changed event.")
        )
    return event_record


def validate_trace(trace, canonical_job_id, errors):
    if not isinstance(trace, list):
        errors.append(error("trace_invalid", "The execution trace is missing."))
        return
    positions = {}
    for item in trace:
        if not isinstance(item, dict):
            errors.append(error("trace_invalid", "The execution trace is malformed."))
            return
        operation = item.get("operation")
        sequence = item.get("sequence")
        if operation in REQUIRED_OPERATIONS:
            if operation in positions or not isinstance(sequence, int):
                errors.append(error("trace_invalid", "The execution trace is ambiguous."))
                return
            positions[operation] = sequence
            if item.get("canonical_job_id") != canonical_job_id:
                errors.append(
                    error(
                        "canonical_job_id_mismatch",
                        "An execution step identifies a different canonical job.",
                    )
                )
    if set(positions) != set(REQUIRED_OPERATIONS):
        errors.append(error("trace_incomplete", "The execution trace is incomplete."))
        return
    report_position = positions["final_report_emitted"]
    if (
        positions["event_appended"] >= report_position
        or positions["current_state_published"] >= report_position
    ):
        errors.append(
            error(
                "persistence_after_report",
                "Both persistence operations must finish before final reporting.",
            )
        )


def validate_statuses(statuses, errors):
    for field in STATUS_FIELDS:
        value = statuses.get(field)
        if value and value not in STATUS_DIMENSIONS[field]:
            errors.append(
                error(
                    "status_value_invalid",
                    "A status value is outside the approved tracker vocabulary.",
                )
            )


def evaluate(response_path, state_path):
    errors = []
    response = read_json(response_path, errors, "response_invalid")
    state = read_json(state_path, errors, "state_invalid")
    if response is None or state is None:
        return errors, None

    state_link = response.get("supporting_state_artifact")
    if (
        not isinstance(state_link, str)
        or not state_link
        or Path(state_link).is_absolute()
    ):
        errors.append(
            error("state_link_invalid", "The response state link is invalid.")
        )
    else:
        try:
            expected_state = (response_path.parent / state_link).resolve()
        except ValueError:
            errors.append(
                error("state_link_invalid", "The response state link is invalid.")
            )
        else:
            if expected_state != state_path.resolve():
                errors.append(
                    error(
                        "state_link_mismatch",
                        "The response links a different state artifact.",
                    )
                )
    scenario_id = response.get("scenario_id")
    state_scenario_id = state.get("scenario_id")
    if not all(
        isinstance(value, str) and value
        for value in (scenario_id, state_scenario_id)
    ):
        errors.append(error("scenario_id_missing", "The scenario ID is missing."))
    elif state_scenario_id != scenario_id:
        errors.append(error("scenario_mismatch", "The evidence belongs to another scenario."))
    canonical_job_id = state.get("canonical_job_id")
    if not isinstance(canonical_job_id, str) or not canonical_job_id:
        errors.append(error("canonical_job_id_missing", "The canonical job ID is missing."))
        return errors, None

    tracker = contained_file(
        state_path.parent,
        state.get("tracker_path"),
        errors,
        "tracker_missing",
    )
    event_log = contained_file(
        state_path.parent,
        state.get("event_log_path"),
        errors,
        "event_log_missing",
    )
    row = read_tracker(tracker, canonical_job_id, errors) if tracker else None
    event_id = state.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        errors.append(error("event_id_missing", "The event ID is missing."))
        event_record = None
    else:
        event_record = (
            read_event(event_log, event_id, canonical_job_id, errors)
            if event_log
            else None
        )

    if tracker and state.get("tracker_sha256") != digest(tracker):
        errors.append(error("tracker_digest_mismatch", "The tracker evidence changed."))
    if event_log and state.get("event_log_sha256") != digest(event_log):
        errors.append(error("event_log_digest_mismatch", "The event evidence changed."))
    if row and event_record:
        to_status = event_record.get("to_status")
        if isinstance(to_status, dict):
            validate_statuses(to_status, errors)
        if not isinstance(to_status, dict) or any(
            to_status.get(field) != row.get(field) for field in STATUS_FIELDS
        ):
            errors.append(
                error(
                    "status_mismatch",
                    "The current-state row and append-only event disagree.",
                )
            )
    validate_trace(state.get("execution_trace"), canonical_job_id, errors)
    return errors, canonical_job_id


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--response", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    arguments = parser.parse_args(argv)
    errors, canonical_job_id = evaluate(arguments.response, arguments.state)
    report = {
        "result": "FAIL" if errors else "PASS",
        "canonical_job_id": canonical_job_id,
        "errors": errors,
    }
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
