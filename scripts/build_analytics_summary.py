"""Build personal analytics from the local Task 6 tracker, without editing it.

CSV timestamps are the sole funnel evidence. Events preserve which canonical
jobs were actionable recommendations, but never supply response or funnel
numerators. Both output formats are rendered from the same summary object.
"""

import argparse
import csv
import json
import os
import pathlib
import sys
import tempfile

from initialise_workspace import validate_contained_paths, validate_private_destination
from update_tracker import (
    APPLICATION_HEADER,
    CURRENT_STATE_FIELDS,
    EVENT_TYPES,
    RANKED_STAGES,
    STATUS_DIMENSIONS,
    open_tracker_file,
    parse_instant,
    TrackerUnreadableError,
)

SOURCES = ("tracker/applications.csv", "tracker/events.jsonl")
OUTPUTS = ("reports/analytics-summary.json", "reports/analytics-summary.md")
TIMESTAMP_FIELDS = (
    "first_seen_at", "last_verified_at", "applied_at", "interview_at",
    "offer_at", "first_response_at", "action_due_at",
)
TERMINAL_OUTCOMES = {"rejected", "withdrawn", "accepted"}
EVENT_FIELDS = {
    "event_id", "occurred_at", "canonical_job_id", "event_type", "from_status",
    "to_status", "jd_snapshot_path", "document_versions", "source", "reason",
    *CURRENT_STATE_FIELDS,
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def instant(value):
    parsed = parse_instant(value)
    require(parsed is not None, "Expected a timezone-aware RFC 3339 timestamp.")
    return parsed


def nonnegative_days(value):
    try:
        days = int(value)
        if days >= 0:
            return days
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("Maturity days must be a nonnegative integer.")


def validate_status(status):
    require(isinstance(status, dict), "Invalid tracker status structure.")
    require(set(status) == set(STATUS_DIMENSIONS), "Invalid tracker status fields.")
    for field, vocabulary in STATUS_DIMENSIONS.items():
        require(status[field] in vocabulary, f"Invalid {field}.")


def read_rows(path):
    handle = open_tracker_file(path)
    require(handle is not None, "Missing tracker/applications.csv.")
    jobs = {}
    with handle:
        reader = csv.reader(handle, strict=True)
        require(tuple(next(reader, ())) == APPLICATION_HEADER,
                "Expected the exact Task 6 applications header.")
        for cells in reader:
            require(len(cells) == len(APPLICATION_HEADER), "Invalid application row width.")
            row = dict(zip(APPLICATION_HEADER, cells))
            identifier = row["canonical_job_id"]
            require(bool(identifier.strip()), "Missing canonical_job_id.")
            validate_status({field: row[field] for field in STATUS_DIMENSIONS})
            for field in TIMESTAMP_FIELDS:
                if row[field]:
                    instant(row[field])
            funnel = [instant(row[field]) for field in
                      ("applied_at", "interview_at", "offer_at") if row[field]]
            require(funnel == sorted(funnel), "Invalid funnel timestamp order.")
            require(identifier not in jobs or jobs[identifier] == row,
                    "Conflicting rows for one canonical_job_id.")
            jobs[identifier] = row
    return jobs


def read_events(path, jobs):
    handle = open_tracker_file(path)
    require(handle is not None, "Missing tracker/events.jsonl.")
    events = []
    with handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            require(isinstance(event, dict) and set(event) == EVENT_FIELDS,
                    "Invalid Task 6 event structure.")
            for field in ("event_id", "canonical_job_id", "source", "reason",
                          *CURRENT_STATE_FIELDS):
                require(isinstance(event[field], str), f"Invalid event {field}.")
            require(bool(event["event_id"].strip()), "Missing event_id.")
            require(bool(event["canonical_job_id"].strip()), "Missing canonical_job_id.")
            require(event["canonical_job_id"] in jobs, "Event has no application row.")
            require(event["event_type"] in EVENT_TYPES, "Invalid event_type.")
            instant(event["occurred_at"])
            for field in ("first_response_at", "action_due_at"):
                if event[field]:
                    instant(event[field])
            validate_status(event["to_status"])
            if event["from_status"] is not None:
                validate_status(event["from_status"])
            require(event["jd_snapshot_path"] is None
                    or isinstance(event["jd_snapshot_path"], str), "Invalid snapshot path.")
            versions = event["document_versions"]
            require(isinstance(versions, dict)
                    and all(isinstance(value, str) for value in versions.values()),
                    "Invalid document_versions.")
            events.append(event)
    return events


def recommended_ids(jobs, events):
    # Screening eligibility alone is not a recommendation. Preserve shortlist
    # history across exits, and keep explicitly submitted jobs in the cohort.
    actionable_stages = set(RANKED_STAGES[RANKED_STAGES.index("shortlisted"):])
    identifiers = {
        identifier for identifier, row in jobs.items()
        if row["application_stage"] in actionable_stages or row["applied_at"]
    }
    identifiers.update(
        event["canonical_job_id"] for event in events
        if any(
            status is not None and status["application_stage"] in actionable_stages
            for status in (event["from_status"], event["to_status"])
        )
    )
    return identifiers


def mature(row, field, days, as_of):
    if not row[field]:
        return False
    age = as_of - instant(row[field])
    return row["application_stage"] in TERMINAL_OUTCOMES or age.days >= days


def metric(cohort, evidence_field, days):
    denominator = len(cohort)
    numerator = sum(bool(row[evidence_field]) for row in cohort)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "maturity_window": {"days": days, "terminal_outcome_is_mature": True},
        "rate": numerator / denominator if denominator else None,
        "data_status": "insufficient_data" if denominator < 10 else "sufficient_data",
    }


def compute_metrics(rows, recommendations, as_of, response_days, interview_days, offer_days):
    return {
        "application_rate": metric(
            [row for row in rows if row["canonical_job_id"] in recommendations],
            "applied_at", 0,
        ),
        "employer_response_rate": metric(
            [row for row in rows if mature(row, "applied_at", response_days, as_of)],
            "first_response_at", response_days,
        ),
        "application_to_interview_rate": metric(
            [row for row in rows if mature(row, "applied_at", interview_days, as_of)],
            "interview_at", interview_days,
        ),
        "interview_to_offer_rate": metric(
            [row for row in rows if mature(row, "interview_at", interview_days, as_of)],
            "offer_at", interview_days,
        ),
        "application_to_offer_rate": metric(
            [row for row in rows if mature(row, "applied_at", offer_days, as_of)],
            "offer_at", offer_days,
        ),
    }


def build_summary(jobs, events, arguments):
    rows = list(jobs.values())
    recommendations = recommended_ids(jobs, events)
    parameters = (recommendations, instant(arguments.as_of), arguments.response_maturity_days,
                  arguments.interview_maturity_days, arguments.offer_maturity_days)
    return {
        "as_of": arguments.as_of,
        "sources": list(SOURCES),
        "overall": compute_metrics(rows, *parameters),
        "segments": {"source": {
            source: {"metrics": compute_metrics(
                [row for row in rows if row["source"] == source], *parameters
            )}
            for source in sorted({row["source"] for row in rows})
        }},
        "ranked_conclusions": [],
    }


def metric_table(metrics):
    lines = [
        "| metric | numerator | denominator | maturity_window_days | rate | data_status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, value in metrics.items():
        rate = "n/a" if value["rate"] is None else f"{value['rate']:.6f}"
        lines.append(
            f"| {name} | {value['numerator']} | {value['denominator']} | "
            f"{value['maturity_window']['days']} | {rate} | {value['data_status']} |"
        )
    return lines


def render_markdown(summary):
    lines = ["# Personal analytics", "", f"As of: {summary['as_of']}", "",
             "Sources: " + ", ".join(summary["sources"]), "", "## Overall", "",
             *metric_table(summary["overall"])]
    for source, segment in summary["segments"]["source"].items():
        label = source.replace("\r", " ").replace("\n", " ") or "(unspecified)"
        lines.extend(["", f"## Source: {label}", "", *metric_table(segment["metrics"])])
    return "\n".join(lines) + "\n"


def publish(workspace, summary):
    paths = [workspace / name for name in OUTPUTS]
    validate_contained_paths(workspace, paths)
    require(all(not path.exists() or path.is_file() for path in paths),
            "Invalid report destination.")
    contents = [json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                render_markdown(summary)]
    paths[0].parent.mkdir(exist_ok=True)
    temporary_paths = []
    published = False
    try:
        for path, content in zip(paths, contents):
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=".analytics-", delete=False,
            ) as handle:
                temporary_paths.append(pathlib.Path(handle.name))
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        validate_contained_paths(workspace, [*paths, *temporary_paths])
        for temporary_path, path in zip(temporary_paths, paths):
            os.replace(temporary_path, path)
            published = True
    except OSError:
        # If the second replacement fails, do not leave a mixed report pair.
        if published:
            for path in paths:
                path.unlink(missing_ok=True)
        raise
    finally:
        for path in temporary_paths:
            path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=pathlib.Path)
    parser.add_argument("--as-of", required=True)
    for name, days in (("response", 30), ("interview", 30), ("offer", 60)):
        parser.add_argument(f"--{name}-maturity-days", type=nonnegative_days, default=days)
    arguments = parser.parse_args()
    try:
        instant(arguments.as_of)
        workspace = validate_private_destination(arguments.workspace)
        require(workspace.is_dir(), "Workspace does not exist.")
        paths = [workspace / name for name in (*SOURCES, *OUTPUTS)]
        validate_contained_paths(workspace, paths)
        jobs = read_rows(paths[0])
        events = read_events(paths[1], jobs)
        summary = build_summary(jobs, events, arguments)
        publish(workspace, summary)
    except (OSError, ValueError, csv.Error, TrackerUnreadableError, RecursionError) as error:
        print(f"Analytics build rejected: {error or type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
