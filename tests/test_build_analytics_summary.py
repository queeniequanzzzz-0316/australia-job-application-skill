"""Task 7 (RED): personal analytics over the exact Task 6 tracker contract.

The analytics builder is a read-only projection of ``applications.csv`` and
``events.jsonl``.  These tests intentionally use the approved 21 CSV columns and
emitted event shape written by Task 6. Employer responses come exclusively
from nonempty ``first_response_at`` cells, never from lifecycle stages or events.
In particular, the current tracker stores
``source`` but not industry, role family, work mode, or candidate segment, so
only source breakdowns can be required without silently changing Task 6.
"""

import csv
import json
import os
import pathlib
import re
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_analytics_summary.py"
AS_OF = "2026-09-15T00:00:00Z"

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

METRICS = (
    "application_rate",
    "employer_response_rate",
    "application_to_interview_rate",
    "interview_to_offer_rate",
    "application_to_offer_rate",
)
METRIC_FIELDS = {
    "numerator",
    "denominator",
    "maturity_window",
    "rate",
    "data_status",
}
DEFAULT_MATURITY_DAYS = {
    "application_rate": 0,
    "employer_response_rate": 30,
    "application_to_interview_rate": 30,
    "interview_to_offer_rate": 30,
    "application_to_offer_rate": 60,
}

INITIAL_STATUS = {
    "discovery_status": "discovered",
    "verification_status": "jd_verified",
    "screening_status": "eligible",
    "application_stage": "shortlisted",
    "action_status": "none",
}


def status(stage, **overrides):
    value = dict(INITIAL_STATUS)
    value["application_stage"] = stage
    value.update(overrides)
    return value


def application(
    canonical_job_id,
    *,
    source="board_a",
    canonical_url=None,
    stage="shortlisted",
    first_seen_at="2026-07-01T00:00:00Z",
    applied_at="",
    interview_at="",
    offer_at="",
    first_response_at="",
    next_action="",
    action_due_at="",
    blocker_category="",
):
    return {
        "canonical_job_id": canonical_job_id,
        "jd_snapshot_path": f"jobs/snapshots/{canonical_job_id}.md",
        "source": source,
        "canonical_url": canonical_url
        or f"https://jobs.example.test/{canonical_job_id}",
        "job_title": "Synthetic Analyst",
        "company": "Fictional Employer Pty Ltd",
        "location": "Melbourne VIC, Australia",
        "discovery_status": "discovered",
        "verification_status": "jd_verified",
        "screening_status": "eligible",
        "application_stage": stage,
        "action_status": "none",
        "first_seen_at": first_seen_at,
        "last_verified_at": first_seen_at,
        "applied_at": applied_at,
        "interview_at": interview_at,
        "offer_at": offer_at,
        "first_response_at": first_response_at,
        "next_action": next_action,
        "action_due_at": action_due_at,
        "blocker_category": blocker_category,
    }


def tracker_event(
    event_id,
    canonical_job_id,
    occurred_at,
    to_stage,
    *,
    source="board_a",
    from_stage=None,
    event_type="status_changed",
    first_response_at="",
    next_action="",
    action_due_at="",
    blocker_category="",
):
    return {
        "event_id": event_id,
        "occurred_at": occurred_at,
        "canonical_job_id": canonical_job_id,
        "event_type": event_type,
        "from_status": None if from_stage is None else status(from_stage),
        "to_status": status(to_stage),
        "jd_snapshot_path": f"jobs/snapshots/{canonical_job_id}.md",
        "document_versions": (
            {"resume": "synthetic-v1"} if event_type == "documents_updated" else {}
        ),
        "source": source,
        "reason": "",
        "first_response_at": first_response_at,
        "next_action": next_action,
        "action_due_at": action_due_at,
        "blocker_category": blocker_category,
    }


def recommendation_event(row, suffix="recommended"):
    return tracker_event(
        f"{row['canonical_job_id']}-{suffix}",
        row["canonical_job_id"],
        row["first_seen_at"],
        "shortlisted",
        source=row["source"],
        from_stage=None,
        event_type="application_recorded",
    )


def file_fingerprint(path):
    stat_result = path.stat()
    return (
        path.read_bytes(),
        stat_result.st_ino,
        stat_result.st_mode,
        stat_result.st_mtime_ns,
    )


class AnalyticsSummaryTests(unittest.TestCase):
    maxDiff = None

    def workspace(self, parent, name="private-workspace"):
        workspace = pathlib.Path(parent) / name
        (workspace / "tracker").mkdir(parents=True)
        return workspace

    def write_tracker(self, workspace, rows, events):
        applications_path = workspace / "tracker" / "applications.csv"
        events_path = workspace / "tracker" / "events.jsonl"
        with applications_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=APPLICATION_HEADER)
            writer.writeheader()
            writer.writerows(rows)
        with events_path.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return applications_path, events_path

    def run_builder(self, workspace, *extra_arguments, extra_environment=None):
        self.assertTrue(
            SCRIPT.is_file(),
            f"Task 7 analytics script is absent: {SCRIPT}",
        )
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        if extra_environment:
            environment.update(extra_environment)
        return subprocess.run(
            [
                "python3",
                "-B",
                str(SCRIPT),
                "--workspace",
                str(workspace),
                "--as-of",
                AS_OF,
                *map(str, extra_arguments),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=environment,
        )

    def assert_success(self, result):
        self.assertEqual(
            result.returncode,
            0,
            f"analytics builder failed\nstdout={result.stdout!r}\nstderr={result.stderr!r}",
        )
        self.assertEqual(result.stderr, "")

    def read_outputs(self, workspace):
        json_path = workspace / "reports" / "analytics-summary.json"
        markdown_path = workspace / "reports" / "analytics-summary.md"
        self.assertTrue(json_path.is_file())
        self.assertTrue(markdown_path.is_file())
        summary = json.loads(json_path.read_text(encoding="utf-8"))
        return summary, markdown_path.read_text(encoding="utf-8")

    def build(self, rows, events, *extra_arguments):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = self.workspace(temporary.name)
        self.write_tracker(workspace, rows, events)
        result = self.run_builder(workspace, *extra_arguments)
        self.assert_success(result)
        summary, markdown = self.read_outputs(workspace)
        return workspace, summary, markdown

    def assert_metric(self, metric, numerator, denominator, days):
        self.assertLessEqual(METRIC_FIELDS, set(metric))
        self.assertEqual(metric["numerator"], numerator)
        self.assertEqual(metric["denominator"], denominator)
        self.assertEqual(metric["maturity_window"]["days"], days)
        self.assertIs(metric["maturity_window"]["terminal_outcome_is_mature"], True)
        expected_rate = None if denominator == 0 else numerator / denominator
        if expected_rate is None:
            self.assertIsNone(metric["rate"])
        else:
            self.assertAlmostEqual(metric["rate"], expected_rate)

    def core_fixture(self):
        shared_url = "https://jobs.example.test/shared-url"
        recommended_only = application("job-recommended", canonical_url=shared_url)
        rejected = application(
            "job-rejected",
            canonical_url=shared_url,
            stage="rejected",
            applied_at="2026-07-01T00:00:00Z",
            first_response_at="2026-07-15T00:00:00Z",
        )
        accepted = application(
            "job-accepted",
            stage="accepted",
            applied_at="2026-07-02T00:00:00Z",
            interview_at="2026-07-20T00:00:00Z",
            offer_at="2026-08-01T00:00:00Z",
            first_response_at="2026-07-20T00:00:00Z",
        )
        rows = [recommended_only, rejected, accepted]
        events = [recommendation_event(row) for row in rows]
        events.extend(
            [
                tracker_event(
                    "rejected-submitted",
                    "job-rejected",
                    rejected["applied_at"],
                    "submitted",
                    from_stage="shortlisted",
                ),
                tracker_event(
                    "rejected-response-recorded",
                    rejected["canonical_job_id"],
                    rejected["first_response_at"],
                    "submitted",
                    from_stage="submitted",
                    event_type="employer_response_recorded",
                    first_response_at=rejected["first_response_at"],
                ),
                tracker_event(
                    "rejected-response",
                    "job-rejected",
                    "2026-07-15T00:00:00Z",
                    "rejected",
                    from_stage="submitted",
                    first_response_at=rejected["first_response_at"],
                ),
                tracker_event(
                    "accepted-submitted",
                    "job-accepted",
                    accepted["applied_at"],
                    "submitted",
                    from_stage="shortlisted",
                ),
                tracker_event(
                    "accepted-interview-recorded",
                    accepted["canonical_job_id"],
                    accepted["first_response_at"],
                    "submitted",
                    from_stage="submitted",
                    event_type="employer_response_recorded",
                    first_response_at=accepted["first_response_at"],
                ),
                tracker_event(
                    "accepted-interview",
                    "job-accepted",
                    accepted["interview_at"],
                    "interview",
                    from_stage="submitted",
                    first_response_at=accepted["first_response_at"],
                ),
                tracker_event(
                    "accepted-offer",
                    "job-accepted",
                    accepted["offer_at"],
                    "offer",
                    from_stage="interview",
                    first_response_at=accepted["first_response_at"],
                ),
                tracker_event(
                    "accepted-final",
                    "job-accepted",
                    "2026-08-02T00:00:00Z",
                    "accepted",
                    from_stage="offer",
                    first_response_at=accepted["first_response_at"],
                ),
            ]
        )
        return rows, events

    def test_five_metrics_use_the_approved_denominators_and_complete_contract(self):
        rows, events = self.core_fixture()
        _, summary, _ = self.build(rows, events)

        self.assertEqual(set(summary["overall"]), set(METRICS))
        expected = {
            "application_rate": (2, 3),
            "employer_response_rate": (2, 2),
            "application_to_interview_rate": (1, 2),
            "interview_to_offer_rate": (1, 1),
            "application_to_offer_rate": (1, 2),
        }
        for name in METRICS:
            with self.subTest(metric=name):
                self.assert_metric(
                    summary["overall"][name],
                    *expected[name],
                    DEFAULT_MATURITY_DAYS[name],
                )

    def test_employer_response_requires_explicit_timestamp_not_stage_or_status_events(self):
        rows = [
            application(
                f"no-response-{stage}",
                stage=stage,
                applied_at="2026-07-01T00:00:00Z",
                interview_at=(
                    "2026-07-10T00:00:00Z" if stage != "rejected" else ""
                ),
                offer_at=(
                    "2026-07-20T00:00:00Z" if stage in ("offer", "accepted") else ""
                ),
                first_response_at="",
            )
            for stage in ("interview", "offer", "rejected", "accepted")
        ]
        events = [recommendation_event(row) for row in rows]
        events.extend(
            tracker_event(
                f"{row['canonical_job_id']}-status",
                row["canonical_job_id"],
                "2026-07-20T00:00:00Z",
                row["application_stage"],
                from_stage="submitted",
                first_response_at="",
            )
            for row in rows
        )
        _, without_response, _ = self.build(rows, events)
        self.assert_metric(
            without_response["overall"]["employer_response_rate"], 0, 4, 30
        )

        submitted = application(
            "explicit-response-still-submitted",
            stage="submitted",
            applied_at="2026-07-01T00:00:00Z",
            first_response_at="2026-07-05T00:00:00Z",
        )
        rows.append(submitted)
        # No response event is supplied: the CSV timestamp is authoritative.
        events.append(recommendation_event(submitted))
        _, with_response, _ = self.build(rows, events)
        self.assert_metric(
            with_response["overall"]["employer_response_rate"], 1, 5, 30
        )

    def test_maturity_uses_funnel_age_or_terminal_outcome_and_overrides_only_days(self):
        recent_rejected = application(
            "recent-rejected",
            stage="rejected",
            first_seen_at="2026-09-13T00:00:00Z",
            applied_at="2026-09-13T00:00:00Z",
            first_response_at="2026-09-14T00:00:00Z",
        )
        recent_withdrawn = application(
            "recent-withdrawn",
            stage="withdrawn",
            first_seen_at="2026-09-12T00:00:00Z",
            applied_at="2026-09-12T00:00:00Z",
        )
        recent_accepted = application(
            "recent-accepted",
            stage="accepted",
            first_seen_at="2026-09-10T00:00:00Z",
            applied_at="2026-09-10T00:00:00Z",
            interview_at="2026-09-11T00:00:00Z",
            offer_at="2026-09-12T00:00:00Z",
            first_response_at="2026-09-11T00:00:00Z",
        )
        old_submitted = application(
            "old-submitted",
            stage="submitted",
            first_seen_at="2026-08-01T00:00:00Z",
            applied_at="2026-08-01T00:00:00Z",
        )
        recent_interview = application(
            "recent-interview",
            stage="interview",
            first_seen_at="2026-09-10T00:00:00Z",
            applied_at="2026-09-10T00:00:00Z",
            interview_at="2026-09-12T00:00:00Z",
            first_response_at="2026-09-12T00:00:00Z",
        )
        rows = [
            recent_rejected,
            recent_withdrawn,
            recent_accepted,
            old_submitted,
            recent_interview,
        ]
        events = [recommendation_event(row) for row in rows]
        events.extend(
            [
                tracker_event(
                    "recent-rejected-response-recorded",
                    recent_rejected["canonical_job_id"],
                    recent_rejected["first_response_at"],
                    "submitted",
                    from_stage="submitted",
                    event_type="employer_response_recorded",
                    first_response_at=recent_rejected["first_response_at"],
                ),
                tracker_event(
                    "recent-rejected-response",
                    "recent-rejected",
                    "2026-09-14T00:00:00Z",
                    "rejected",
                    from_stage="submitted",
                    first_response_at=recent_rejected["first_response_at"],
                ),
                tracker_event(
                    "recent-accepted-interview-recorded",
                    recent_accepted["canonical_job_id"],
                    recent_accepted["first_response_at"],
                    "submitted",
                    from_stage="submitted",
                    event_type="employer_response_recorded",
                    first_response_at=recent_accepted["first_response_at"],
                ),
                tracker_event(
                    "recent-accepted-interview",
                    "recent-accepted",
                    recent_accepted["interview_at"],
                    "interview",
                    from_stage="submitted",
                    first_response_at=recent_accepted["first_response_at"],
                ),
                tracker_event(
                    "recent-accepted-offer",
                    "recent-accepted",
                    recent_accepted["offer_at"],
                    "offer",
                    from_stage="interview",
                    first_response_at=recent_accepted["first_response_at"],
                ),
                tracker_event(
                    "recent-interview-event-recorded",
                    recent_interview["canonical_job_id"],
                    recent_interview["first_response_at"],
                    "submitted",
                    from_stage="submitted",
                    event_type="employer_response_recorded",
                    first_response_at=recent_interview["first_response_at"],
                ),
                tracker_event(
                    "recent-interview-event",
                    "recent-interview",
                    recent_interview["interview_at"],
                    "interview",
                    from_stage="submitted",
                    first_response_at=recent_interview["first_response_at"],
                ),
            ]
        )

        _, default, _ = self.build(rows, events)
        _, overridden, _ = self.build(
            rows,
            events,
            "--response-maturity-days",
            "0",
            "--interview-maturity-days",
            "0",
            "--offer-maturity-days",
            "0",
        )

        # The recent interview has an explicit response but is outside the
        # default submitted cohort. Zero-day windows include all three responses.
        # Interview-to-offer ages from interview_at (30 days), with terminal
        # outcomes mature immediately; application-to-offer ages from applied_at.
        default_expected = {
            "application_rate": (5, 5, 0),
            "employer_response_rate": (2, 4, 30),
            "application_to_interview_rate": (1, 4, 30),
            "interview_to_offer_rate": (1, 1, 30),
            "application_to_offer_rate": (1, 3, 60),
        }
        override_expected = {
            "application_rate": (5, 5, 0),
            "employer_response_rate": (3, 5, 0),
            "application_to_interview_rate": (2, 5, 0),
            "interview_to_offer_rate": (1, 2, 0),
            "application_to_offer_rate": (1, 5, 0),
        }
        for name in METRICS:
            with self.subTest(metric=name, window="default"):
                self.assert_metric(default["overall"][name], *default_expected[name])
            with self.subTest(metric=name, window="override"):
                self.assert_metric(
                    overridden["overall"][name], *override_expected[name]
                )

    def test_interview_to_offer_maturity_ages_from_interview_not_submission(self):
        rows = [
            application(
                "mature-interview",
                stage="interview",
                applied_at="2026-07-01T00:00:00Z",
                interview_at="2026-07-20T00:00:00Z",
            ),
            application(
                "old-application-recent-interview",
                stage="interview",
                applied_at="2026-07-01T00:00:00Z",
                interview_at="2026-09-12T00:00:00Z",
            ),
            application(
                "terminal-recent-interview",
                stage="accepted",
                applied_at="2026-09-10T00:00:00Z",
                interview_at="2026-09-11T00:00:00Z",
                offer_at="2026-09-12T00:00:00Z",
            ),
        ]
        events = [recommendation_event(row) for row in rows]
        _, default, _ = self.build(rows, events)
        self.assert_metric(default["overall"]["application_to_interview_rate"], 3, 3, 30)
        self.assert_metric(default["overall"]["interview_to_offer_rate"], 1, 2, 30)

        _, offer_override, _ = self.build(rows, events, "--offer-maturity-days", "0")
        self.assert_metric(
            offer_override["overall"]["interview_to_offer_rate"], 1, 2, 30
        )
        self.assert_metric(
            offer_override["overall"]["application_to_offer_rate"], 1, 3, 0
        )
        _, interview_override, _ = self.build(
            rows, events, "--interview-maturity-days", "0"
        )
        self.assert_metric(
            interview_override["overall"]["interview_to_offer_rate"], 1, 3, 0
        )

    def test_actionable_recommendations_deduplicate_only_by_canonical_job_id(self):
        same_url = "https://jobs.example.test/the-same-url"
        rows = [
            application("job-a", canonical_url=same_url),
            application("job-b", canonical_url=same_url),
            application(
                "job-c",
                stage="submitted",
                applied_at="2026-07-01T00:00:00Z",
            ),
        ]
        events = [recommendation_event(row) for row in rows]
        events.extend(
            [
                tracker_event(
                    "job-a-documents",
                    "job-a",
                    "2026-07-02T00:00:00Z",
                    "shortlisted",
                    from_stage="shortlisted",
                    event_type="documents_updated",
                ),
                tracker_event(
                    "job-a-preparing",
                    "job-a",
                    "2026-07-03T00:00:00Z",
                    "preparing",
                    from_stage="shortlisted",
                ),
                tracker_event(
                    "job-c-submitted",
                    "job-c",
                    "2026-07-01T00:00:00Z",
                    "submitted",
                    from_stage="shortlisted",
                ),
            ]
        )

        _, summary, _ = self.build(rows, events)

        self.assert_metric(summary["overall"]["application_rate"], 1, 3, 0)

    def test_actionable_recommendations_require_stage_history_or_submission_evidence(self):
        eligible_only = application("eligible-only", stage="not_started")
        current = application("currently-shortlisted")
        historical = application("previously-shortlisted", stage="withdrawn")
        # Submission evidence survives an exit, even with no shortlist history.
        sparse_submitted = application(
            "sparse-submitted",
            stage="withdrawn",
            applied_at="2026-07-01T00:00:00Z",
        )
        from_history = application("historical-preparing", stage="rejected")
        eligible_events = [tracker_event(
            "eligible-recorded", eligible_only["canonical_job_id"],
            eligible_only["first_seen_at"], "not_started",
            event_type="application_recorded",
        )]
        historical_events = [
            recommendation_event(historical),
            recommendation_event(historical, "recommended-again"),
            tracker_event(
                "historical-withdrawn", historical["canonical_job_id"],
                "2026-07-02T00:00:00Z", "withdrawn", from_stage="shortlisted",
            ),
        ]
        from_events = [tracker_event(
            "historical-rejected", from_history["canonical_job_id"],
            "2026-07-02T00:00:00Z", "rejected", from_stage="preparing",
        )]
        cases = [
            ("eligible_not_started", [eligible_only], eligible_events, 0, 0),
            ("current_shortlist", [current], [], 0, 1),
            ("historical_shortlist_deduplicated", [historical], historical_events, 0, 1),
            ("sparse_submission", [sparse_submitted], [], 1, 1),
            ("historical_from_status", [from_history], from_events, 0, 1),
            (
                "combined", [eligible_only, current, historical, sparse_submitted, from_history],
                eligible_events + historical_events + from_events, 1, 4,
            ),
        ]
        for label, rows, events, numerator, denominator in cases:
            with self.subTest(case=label):
                _, summary, _ = self.build(rows, events)
                self.assert_metric(
                    summary["overall"]["application_rate"], numerator, denominator, 0
                )

        # A later ranked stage remains actionable without screening eligibility.
        later = application("currently-preparing", stage="preparing")
        later["screening_status"] = "verified"
        _, summary, _ = self.build([later], [])
        self.assert_metric(summary["overall"]["application_rate"], 0, 1, 0)

    def test_source_segments_below_ten_are_insufficient_and_not_ranked(self):
        rows = []
        for source, count in (("board_a", 9), ("board_b", 8)):
            rows.extend(
                application(f"{source}-{index}", source=source)
                for index in range(count)
            )
        events = [recommendation_event(row) for row in rows]

        _, summary, markdown = self.build(rows, events)

        self.assertEqual(set(summary["segments"]), {"source"})
        self.assertEqual(set(summary["segments"]["source"]), {"board_a", "board_b"})
        unavailable = {
            "industry",
            "role_family",
            "work_mode",
            "onshore_offshore",
            "professional_casual_part_time",
        }
        self.assertTrue(unavailable.isdisjoint(summary["segments"]))
        for source in ("board_a", "board_b"):
            metrics = summary["segments"]["source"][source]["metrics"]
            self.assertEqual(set(metrics), set(METRICS))
            for name, metric in metrics.items():
                with self.subTest(source=source, metric=name):
                    self.assertEqual(metric["data_status"], "insufficient_data")
        self.assertEqual(summary.get("ranked_conclusions", []), [])
        self.assertNotRegex(
            markdown.lower(),
            r"\b(best|worst|top[- ]performing|strong performance|outperforms?)\b",
        )

    def test_segment_sample_uses_eligible_metric_cohort_not_total_recommendations(self):
        rows = [
            application(
                f"submitted-{index}",
                stage="submitted",
                applied_at="2026-07-01T00:00:00Z",
                first_response_at="2026-07-05T00:00:00Z",
            )
            for index in range(9)
        ]
        rows.extend(application(f"recommended-{index}") for index in range(3))
        _, summary, markdown = self.build(
            rows, [recommendation_event(row) for row in rows]
        )
        metrics = summary["segments"]["source"]["board_a"]["metrics"]
        self.assert_metric(metrics["application_rate"], 9, 12, 0)
        self.assert_metric(metrics["employer_response_rate"], 9, 9, 30)
        self.assertEqual(
            metrics["employer_response_rate"]["data_status"], "insufficient_data"
        )
        self.assertEqual(summary.get("ranked_conclusions", []), [])
        self.assertNotRegex(
            markdown.lower(),
            r"\b(best|worst|top[- ]performing|strong performance|outperforms?)\b",
        )

    def test_json_and_markdown_publish_matching_values_from_one_computation(self):
        rows, events = self.core_fixture()
        workspace, summary, markdown = self.build(rows, events)

        self.assertIn("tracker/applications.csv", markdown)
        self.assertIn("tracker/events.jsonl", markdown)
        self.assertIn("tracker/applications.csv", json.dumps(summary))
        self.assertIn("tracker/events.jsonl", json.dumps(summary))
        for name, metric in summary["overall"].items():
            rate_text = "n/a" if metric["rate"] is None else f"{metric['rate']:.6f}"
            row_pattern = (
                rf"^\|\s*{re.escape(name)}\s*\|\s*{metric['numerator']}\s*\|"
                rf"\s*{metric['denominator']}\s*\|\s*"
                rf"{metric['maturity_window']['days']}\s*\|\s*"
                rf"{re.escape(rate_text)}\s*\|\s*"
                rf"{re.escape(metric['data_status'])}\s*\|$"
            )
            self.assertRegex(markdown, re.compile(row_pattern, re.MULTILINE))
        self.assertEqual(
            {path.name for path in (workspace / "reports").iterdir()},
            {"analytics-summary.json", "analytics-summary.md"},
        )

    def test_local_analytics_ignore_consent_and_cross_user_data_with_network_blocked(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            parent = pathlib.Path(temporary_directory)
            local = self.workspace(parent, "local-candidate")
            other = self.workspace(parent, "other-candidate")
            blocker = parent / "network-blocker"
            blocker.mkdir()
            (blocker / "sitecustomize.py").write_text(
                "import socket\n"
                "def denied(*args, **kwargs):\n"
                "    raise AssertionError('network access is forbidden')\n"
                "socket.socket = denied\n"
                "socket.create_connection = denied\n",
                encoding="utf-8",
            )
            (local / "profile").mkdir()
            (local / "profile" / "analytics-consent.yaml").write_text(
                "this is deliberately not valid consent yaml: [\n", encoding="utf-8"
            )
            local_row = application("local-job")
            self.write_tracker(local, [local_row], [recommendation_event(local_row)])
            other_rows = [application(f"other-job-{index}") for index in range(20)]
            self.write_tracker(other, other_rows, [recommendation_event(r) for r in other_rows])
            other_before = {
                path.relative_to(other): file_fingerprint(path)
                for path in other.rglob("*")
                if path.is_file()
            }

            result = self.run_builder(
                local,
                extra_environment={"PYTHONPATH": str(blocker)},
            )
            self.assert_success(result)
            summary, _ = self.read_outputs(local)

            self.assertEqual(summary["overall"]["application_rate"]["denominator"], 1)
            self.assertFalse((other / "reports").exists())
            self.assertEqual(
                {
                    path.relative_to(other): file_fingerprint(path)
                    for path in other.rglob("*")
                    if path.is_file()
                },
                other_before,
            )

    def test_missing_or_malformed_tracker_inputs_fail_closed_without_outputs(self):
        cases = {
            "missing_applications": (None, ""),
            "missing_events": (",".join(APPLICATION_HEADER) + "\n", None),
            "obsolete_17_column_header": (
                ",".join(APPLICATION_HEADER[:17]) + "\n", ""
            ),
            "malformed_applications": ("not,the,task6,header\n1,2,3,4\n", ""),
            "malformed_events": (
                ",".join(APPLICATION_HEADER) + "\n",
                "{not-json}\n",
            ),
        }
        for name, (applications_text, events_text) in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temporary:
                workspace = self.workspace(temporary)
                if applications_text is not None:
                    (workspace / "tracker" / "applications.csv").write_text(
                        applications_text, encoding="utf-8"
                    )
                if events_text is not None:
                    (workspace / "tracker" / "events.jsonl").write_text(
                        events_text, encoding="utf-8"
                    )

                result = self.run_builder(workspace)

                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(
                    (workspace / "reports" / "analytics-summary.json").exists()
                )
                self.assertFalse(
                    (workspace / "reports" / "analytics-summary.md").exists()
                )

    def test_successful_build_does_not_mutate_tracker_sources(self):
        rows, events = self.core_fixture()
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = self.workspace(temporary_directory)
            applications_path, events_path = self.write_tracker(workspace, rows, events)
            fixed_ns = 1_600_000_000_000_000_000
            os.utime(applications_path, ns=(fixed_ns, fixed_ns))
            os.utime(events_path, ns=(fixed_ns, fixed_ns))
            before = {
                applications_path: file_fingerprint(applications_path),
                events_path: file_fingerprint(events_path),
            }

            result = self.run_builder(workspace)

            self.assert_success(result)
            self.read_outputs(workspace)
            self.assertEqual(
                {path: file_fingerprint(path) for path in before},
                before,
            )


if __name__ == "__main__":
    unittest.main()
