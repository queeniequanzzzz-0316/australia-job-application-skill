"""Task 6 (RED): the tracker updater's contract.

``update_tracker.py`` is the only writer of the two tracker files.  It keeps
``tracker/applications.csv`` as the current state -- exactly one row per
canonical_job_id -- and ``tracker/events.jsonl`` as an append-only history.

Two invariants dominate this file.  The first is that the five status
dimensions never collapse into one field: discovery, verification, screening,
application stage and action each have their own disjoint vocabulary, their own
column, their own error code, and their own key inside every event's
``from_status``/``to_status`` objects.  The second is the transaction boundary:
the run touches two files, so it prechecks that they agree, proves the CSV
write can succeed, makes the event durable, and only then publishes the state.
A state change that lost its event is unrecoverable; an event whose state never
published is detectable and replayable, so that is the failure that is allowed
to happen.

Two things are pinned here that the first round got wrong.  The vocabularies
are the approved Design Spec's, not invented ones: every retired value is fed
back to the dimension that used to accept it and must now be refused, and the
two extra dimensions gave up the values that collided with the approved three
so the whole set stays pairwise disjoint.  And the funnel timestamps are
"or later", not "exactly this stage": reaching ``offer`` stamps ``applied_at``
and ``interview_at`` too, because an application at offer was submitted and
interviewed whether or not anyone recorded those steps.
"""

import contextlib
import csv
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update_tracker.py"
SAVER = ROOT / "scripts" / "save_job_snapshot.py"
INITIALISER = ROOT / "scripts" / "initialise_workspace.py"
JOB_FIXTURE = ROOT / "tests" / "fixtures" / "sample-job.json"
APPLICATION_FIXTURE = ROOT / "tests" / "fixtures" / "sample-application.json"

REPORT_KEYS = {
    "status",
    "canonical_job_id",
    "jd_snapshot_path",
    "event_id",
    "event_recorded",
    "errors",
}
DIAGNOSTIC_KEYS = {"code", "field", "message"}
STATUSES = {"created", "updated", "unchanged", "rejected"}

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
]

DESCRIPTIVE_COLUMNS = ("source", "canonical_url", "job_title", "company", "location")

# -- the five dimensions, plus the event vocabulary -------------------------

# The two extra dimensions.  Their vocabularies were renamed where they
# collided with the approved three: pairwise disjointness is what makes "a
# value belonging to another dimension is refused for this one" testable, and
# the approved three are fixed, so the collisions were resolved on this side.
DISCOVERY_STATUSES = ("discovered", "duplicate_of_known", "expired", "delisted")
VERIFICATION_STATUSES = (
    "unverified",
    "jd_retrieved",
    "jd_verified",
    "verification_failed",
)

# The three approved Design Spec vocabularies, exactly as approved.  They are
# not derived, extended or reordered here: the first value of each is also the
# value an insert records for a dimension the record left out.
SCREENING_STATUSES = (
    "new",
    "verified",
    "eligible",
    "excluded",
    "duplicate",
    "closed",
)
APPLICATION_STAGES = (
    "not_started",
    "shortlisted",
    "preparing",
    "ready",
    "submitted",
    "interview",
    "offer",
    "accepted",
    "rejected",
    "withdrawn",
)
ACTION_STATUSES = (
    "none",
    "candidate_review",
    "document_edit",
    "application_form",
    "follow_up",
    "blocked",
)
EVENT_TYPES = (
    "application_recorded",
    "status_changed",
    "snapshot_linked",
    "documents_updated",
    "note_recorded",
)

VOCABULARIES = {
    "discovery_status": DISCOVERY_STATUSES,
    "verification_status": VERIFICATION_STATUSES,
    "screening_status": SCREENING_STATUSES,
    "application_stage": APPLICATION_STAGES,
    "action_status": ACTION_STATUSES,
    "event_type": EVENT_TYPES,
}

# Every value the earlier vocabularies carried and the approved ones do not,
# mapped to the dimension that used to accept it.  Each must now be refused by
# exactly that dimension, or the retired vocabulary is still in the code.
RETIRED_VALUES = (
    ("screening_status", "not_screened"),
    ("screening_status", "screened_in"),
    ("screening_status", "screened_out"),
    ("screening_status", "screening_needs_review"),
    ("application_stage", "identified"),
    ("application_stage", "unsuccessful"),
    ("application_stage", "candidate_withdrew"),
    ("action_status", "awaiting_candidate"),
    ("action_status", "awaiting_employer"),
    ("action_status", "follow_up_due"),
)

# The three values the two extra dimensions had to give up, mapped to the
# dimension that used to hold them.  ``duplicate`` and ``verified`` now belong
# to screening_status and ``closed`` moved there too, so each of these is a
# cross-dimension borrow and must be refused where it used to be accepted.
RENAMED_AWAY_VALUES = (
    ("discovery_status", "duplicate"),
    ("verification_status", "verified"),
    ("action_status", "closed"),
)

# One value per dimension, used to prove that a value belonging to a different
# dimension is refused rather than quietly accepted.
REPRESENTATIVE_VALUES = {
    "discovery_status": "duplicate_of_known",
    "verification_status": "jd_verified",
    "screening_status": "eligible",
    "application_stage": "accepted",
    "action_status": "blocked",
    "event_type": "status_changed",
}

STATUS_KEYS = (
    "discovery_status",
    "verification_status",
    "screening_status",
    "application_stage",
    "action_status",
)

# What an insert records for a dimension the record left out.  application_stage
# is absent because it is required, so an insert can never need a default.
INITIAL_STATUS = {
    "discovery_status": "discovered",
    "verification_status": "unverified",
    "screening_status": "new",
    "action_status": "none",
}

GATED_STAGES = (
    "shortlisted",
    "preparing",
    "ready",
    "submitted",
    "interview",
    "offer",
)
UNGATED_STAGES = ("not_started", "accepted", "rejected", "withdrawn")

# -- the funnel ------------------------------------------------------------

# The ranked walk through the funnel.  "Reaching" a stage means reaching it or
# any later one, so the rank is what decides which timestamp cells a stage
# stamps.  ``rejected`` and ``withdrawn`` are terminal exits with no rank: they
# are left out of this tuple deliberately, not forgotten.
RANKED_STAGES = (
    "not_started",
    "shortlisted",
    "preparing",
    "ready",
    "submitted",
    "interview",
    "offer",
    "accepted",
)
TERMINAL_STAGES = ("rejected", "withdrawn")
STAGE_RANK = {stage: rank for rank, stage in enumerate(RANKED_STAGES)}

FUNNEL_COLUMNS = ("applied_at", "interview_at", "offer_at")
# The stage each funnel column is first stamped at; a later stage stamps it too.
FUNNEL_THRESHOLDS = {
    "applied_at": "submitted",
    "interview_at": "interview",
    "offer_at": "offer",
}


def funnel_columns_reached_by(stage):
    """The funnel columns ``stage`` stamps, because it is that stage or later."""
    if stage not in STAGE_RANK:
        return ()
    return tuple(
        column
        for column in FUNNEL_COLUMNS
        if STAGE_RANK[stage] >= STAGE_RANK[FUNNEL_THRESHOLDS[column]]
    )


def expected_funnel(walk):
    """The three funnel cells after ``walk``, a sequence of (stage, occurred_at).

    A cell takes the occurred_at of the step that first reached it and is never
    written again, which is the whole contract in four lines.
    """
    cells = {column: "" for column in FUNNEL_COLUMNS}
    for stage, occurred_at in walk:
        for column in funnel_columns_reached_by(stage):
            if not cells[column]:
                cells[column] = occurred_at
    return cells

EVENT_KEYS = {
    "event_id",
    "occurred_at",
    "canonical_job_id",
    "event_type",
    "from_status",
    "to_status",
    "jd_snapshot_path",
    "document_versions",
    "source",
}

REQUIRED_FIELDS = (
    "canonical_job_id",
    "occurred_at",
    "event_type",
    "source",
    "application_stage",
)

SNAPSHOT_FRONTMATTER_KEYS = (
    "canonical_job_id",
    "retrieved_at",
    "last_verified_at",
    "source",
    "canonical_url",
    "job_title",
    "company",
    "location",
    "content_digest",
)

SUBPROCESS_TIMEOUT_SECONDS = 60
NON_REGULAR_TIMEOUT_SECONDS = 10
PINNED_MTIME_NS = 1600000000000000000
RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

MISSING = object()

VALID_RECORD = {
    "canonical_job_id": "job-1",
    "occurred_at": "2026-09-12T07:00:00Z",
    "event_type": "application_recorded",
    "source": "example_board",
    "application_stage": "not_started",
}

# The advertisement Task 5 turns into the snapshot Task 6 links.  Its
# descriptive fields are deliberately different from the record's ``source``
# so the tracker cannot pass the descriptive-column tests by copying the
# record instead of reading the snapshot it linked.
SNAPSHOT_JOB = {
    "canonical_job_id": "job-1",
    "retrieved_at": "2026-09-11T07:00:54Z",
    "last_verified_at": "2026-09-11T07:00:54Z",
    "source": "snapshot_board",
    "canonical_url": "https://example.com/jobs/1042",
    "job_title": "Backend Engineer",
    "company": "Southern Cross Analytics Pty Ltd",
    "location": "Melbourne VIC 3000, Australia",
    "job_description": "We are hiring a backend engineer in Melbourne.\n",
}

BASE_SNAPSHOT_PATH = "jobs/snapshots/job-1.md"


def record_with(**overrides):
    record = dict(VALID_RECORD)
    record.update(overrides)
    return record


def record_without(*keys):
    record = dict(VALID_RECORD)
    for key in keys:
        record.pop(key, None)
    return record


def status_object(application_stage, **overrides):
    status = dict(INITIAL_STATUS)
    status["application_stage"] = application_stage
    status.update(overrides)
    return status


def job_with(**overrides):
    job = dict(SNAPSHOT_JOB)
    job.update(overrides)
    return job


def snapshot_tree(root):
    """Map every path under ``root`` to its bytes/mtime so writes are provable.

    Symlinks are recorded by target and never followed, so a write *through* a
    symlink shows up as a change to whatever the symlink points at.
    """
    entries = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_symlink():
            entries[key] = ("symlink", os.readlink(path))
        elif path.is_dir():
            entries[key] = ("directory", None)
        elif path.is_file():
            status = path.stat()
            try:
                content = path.read_bytes()
            except OSError:
                content = ("unreadable", status.st_size)
            entries[key] = ("file", content, status.st_mtime_ns)
        else:
            entries[key] = ("other", None)
    return entries


class Sandbox:
    """A temporary parent holding the workspace, a sibling that must stay
    untouched, and an inbox for records."""

    def __init__(self, root):
        self.root = root
        self.workspace = root / "private-workspace"
        self.outside = root / "outside-private-data"
        self.inbox = root / "inbox"

    @property
    def tracker(self):
        return self.workspace / "tracker"

    @property
    def applications_csv(self):
        return self.workspace / "tracker" / "applications.csv"

    @property
    def events_jsonl(self):
        return self.workspace / "tracker" / "events.jsonl"

    @property
    def snapshots(self):
        return self.workspace / "jobs" / "snapshots"

    def secrets(self):
        return (
            str(self.root),
            str(self.workspace),
            str(self.outside),
            str(self.inbox),
            self.workspace.name,
            self.outside.name,
        )


class UpdateTrackerTestCase(unittest.TestCase):
    maxDiff = None

    # -- process plumbing --------------------------------------------------

    def run_tracker(self, *arguments, timeout=SUBPROCESS_TIMEOUT_SECONDS):
        try:
            return subprocess.run(
                ["python3", str(SCRIPT), *map(str, arguments)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except subprocess.TimeoutExpired:
            self.fail(
                f"update_tracker did not finish within {timeout}s for arguments "
                f"{tuple(map(str, arguments))!r}; it must always terminate with a "
                "JSON report"
            )

    def initialise_workspace(self, destination):
        result = subprocess.run(
            ["python3", str(INITIALISER), "--destination", str(destination)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @contextlib.contextmanager
    def sandbox(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            box = Sandbox(pathlib.Path(temporary_directory))
            box.outside.mkdir()
            (box.outside / "keep.txt").write_text("private\n", encoding="utf-8")
            box.inbox.mkdir()
            self.initialise_workspace(box.workspace)
            yield box

    # -- building genuine Task 5 snapshots ---------------------------------

    def save_snapshot(self, box, job=MISSING, name="snapshot-job.json"):
        """Create a real snapshot with the real Task 5 script and return the
        exact workspace-relative path string Task 5 reported."""
        record = SNAPSHOT_JOB if job is MISSING else job
        path = box.inbox / name
        path.write_text(json.dumps(record), encoding="utf-8")
        result = subprocess.run(
            [
                "python3",
                str(SAVER),
                "--workspace",
                str(box.workspace),
                "--input-json",
                str(path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["errors"], [])
        self.assertIsInstance(report["jd_snapshot_path"], str)
        return report["jd_snapshot_path"]

    # -- report shape ------------------------------------------------------

    def parse_report(self, result, secrets=()):
        stdout = result.stdout
        try:
            report = json.loads(stdout)
        except ValueError:
            self.fail(
                "update_tracker must print exactly one JSON object on stdout; got "
                f"returncode={result.returncode} stdout={stdout!r} "
                f"stderr={result.stderr!r}"
            )
        self.assertIsInstance(report, dict, f"report must be a JSON object: {stdout!r}")
        self.assertEqual(set(report), REPORT_KEYS, "unexpected top-level report keys")
        self.assertEqual(
            len(stdout.strip().splitlines()),
            1,
            f"stdout must be exactly one JSON object: {stdout!r}",
        )
        self.assertIn(report["status"], STATUSES)
        self.assertIsInstance(report["errors"], list)
        for entry in report["errors"]:
            self.assertIsInstance(entry, dict)
            self.assertEqual(set(entry), DIAGNOSTIC_KEYS, "bad error entry")
            for key in DIAGNOSTIC_KEYS:
                self.assertIsInstance(entry[key], str)
        rejected = bool(report["errors"])
        self.assertEqual(
            report["status"] == "rejected",
            rejected,
            "status must be 'rejected' exactly when errors is non-empty",
        )
        self.assertIsInstance(report["event_recorded"], bool)
        if report["event_recorded"]:
            self.assertIsInstance(
                report["event_id"], str, "a recorded event must be named"
            )
            self.assertTrue(report["event_id"].strip())
        else:
            self.assertIsNone(
                report["event_id"], "event_id must be null when no event was recorded"
            )
        if report["canonical_job_id"] is not None:
            self.assertIsInstance(report["canonical_job_id"], str)
        if report["jd_snapshot_path"] is not None:
            self.assertIsInstance(report["jd_snapshot_path"], str)
            self.assertFalse(os.path.isabs(report["jd_snapshot_path"]))
            self.assertNotIn("\\", report["jd_snapshot_path"])
        self.assertEqual(
            result.returncode,
            1 if rejected else 0,
            f"exit code must be 0 iff errors is empty; stderr={result.stderr!r}",
        )
        self.assertEqual(
            result.stderr, "", "stderr must stay empty; no tracebacks, no chatter"
        )
        for secret in secrets:
            self.assertNotIn(
                str(secret),
                stdout,
                "the report must never disclose a filesystem path",
            )
        return report

    def codes(self, entries):
        return [entry["code"] for entry in entries]

    def fields_for(self, entries, code):
        return [entry["field"] for entry in entries if entry["code"] == code]

    # -- driving the script ------------------------------------------------

    def write_record(self, box, record=MISSING, raw=None, name="application.json"):
        if raw is None:
            raw = json.dumps(
                VALID_RECORD if record is MISSING else record, ensure_ascii=False
            )
        path = box.inbox / name
        path.write_text(raw, encoding="utf-8")
        return path

    def update(
        self,
        box,
        record=MISSING,
        raw=None,
        workspace=MISSING,
        record_path=None,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    ):
        if record_path is None:
            record_path = self.write_record(box, record=record, raw=raw)
        workspace_argument = box.workspace if workspace is MISSING else workspace
        result = self.run_tracker(
            "--workspace",
            workspace_argument,
            "--input-json",
            record_path,
            timeout=timeout,
        )
        report = self.parse_report(
            result,
            secrets=(*box.secrets(), str(record_path), str(workspace_argument)),
        )
        return result, report

    def update_ok(self, box, record=MISSING, **keywords):
        result, report = self.update(box, record=record, **keywords)
        self.assertEqual(report["errors"], [], f"unexpected errors: {report['errors']}")
        self.assertEqual(result.returncode, 0)
        return report

    def assert_rejected(self, result, report, code, field):
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["status"], "rejected")
        self.assertIn(
            code, self.codes(report["errors"]), f"errors were {report['errors']}"
        )
        if field is not None:
            self.assertEqual(self.fields_for(report["errors"], code), [field])

    def update_expecting_rejection(
        self,
        box,
        code,
        field,
        record=MISSING,
        raw=None,
        workspace=MISSING,
        record_path=None,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    ):
        if record_path is None:
            record_path = self.write_record(box, record=record, raw=raw)
        before = snapshot_tree(box.root)

        result, report = self.update(
            box, record_path=record_path, workspace=workspace, timeout=timeout
        )

        self.assertEqual(
            snapshot_tree(box.root),
            before,
            "a rejected run must not create, change or delete a single byte anywhere",
        )
        self.assert_rejected(result, report, code, field)
        self.assertFalse(
            report["event_recorded"],
            "a rejection before the event is written must not claim an event",
        )
        return result, report

    # -- reading the two tracker files -------------------------------------

    def csv_rows(self, box):
        with box.applications_csv.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        self.assertTrue(rows, "applications.csv must always keep its header")
        self.assertEqual(
            rows[0], APPLICATION_HEADER, "the header must be the agreed 17 columns"
        )
        for row in rows[1:]:
            self.assertEqual(
                len(row),
                len(APPLICATION_HEADER),
                f"every row must carry all 17 columns: {row!r}",
            )
        return [dict(zip(APPLICATION_HEADER, row)) for row in rows[1:]]

    def row_for(self, box, canonical_job_id="job-1"):
        matches = [
            row
            for row in self.csv_rows(box)
            if row["canonical_job_id"] == canonical_job_id
        ]
        self.assertEqual(
            len(matches), 1, f"exactly one row per canonical_job_id: {matches!r}"
        )
        return matches[0]

    def row_ids(self, box):
        return [row["canonical_job_id"] for row in self.csv_rows(box)]

    def events(self, box):
        text = box.events_jsonl.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def assert_event_shape(self, event):
        self.assertLessEqual(
            EVENT_KEYS, set(event), f"the event is missing agreed keys: {event!r}"
        )
        self.assertIsInstance(event["event_id"], str)
        self.assertTrue(event["event_id"].strip())
        self.assertIsInstance(event["occurred_at"], str)
        self.assertIsInstance(event["canonical_job_id"], str)
        self.assertIn(event["event_type"], EVENT_TYPES)
        self.assertIsInstance(event["document_versions"], dict)
        self.assertIsInstance(event["to_status"], dict)
        self.assertEqual(
            set(event["to_status"]),
            set(STATUS_KEYS),
            "to_status must keep all five dimensions separate",
        )
        if event["from_status"] is not None:
            self.assertIsInstance(event["from_status"], dict)
            self.assertEqual(
                set(event["from_status"]),
                set(STATUS_KEYS),
                "from_status must keep all five dimensions separate",
            )

    def pin_mtime(self, path):
        os.utime(path, ns=(PINNED_MTIME_NS, PINNED_MTIME_NS))
        self.assertEqual(path.stat().st_mtime_ns, PINNED_MTIME_NS)

    def assert_untouched(self, path, expected_bytes):
        self.assertEqual(path.read_bytes(), expected_bytes, "file was rewritten")
        self.assertEqual(
            path.stat().st_mtime_ns,
            PINNED_MTIME_NS,
            "file was rewritten (mtime moved) when it had to be left alone",
        )

    # -- convenience for the common "insert one job" opening ---------------

    def insert(self, box, record=MISSING, **keywords):
        report = self.update_ok(box, record=record, **keywords)
        self.assertEqual(report["status"], "created")
        return report

    def link_snapshot(self, box):
        """Create a real Task 5 snapshot and return its reported path."""
        return self.save_snapshot(box)

    def snapshot_bytes(self, box):
        """The bytes of a genuine Task 5 snapshot, so a copy planted outside
        the workspace is a legitimate snapshot in every respect except where
        it sits: containment is then the only thing left to object to."""
        self.save_snapshot(box)
        return (box.workspace / BASE_SNAPSHOT_PATH).read_bytes()


UNSAFE_JOB_IDS = (
    ("parent_escape", "../escape"),
    ("dot_dot", ".."),
    ("dot", "."),
    ("embedded_parent", "a/../b"),
    ("absolute", "/absolute/id"),
    ("nested_segment", "jobs/x"),
    ("backslash", "a\\b"),
    ("home_relative", "~/x"),
    ("empty", ""),
    ("blank", "   "),
    ("hidden_dotfile", ".hidden"),
    ("leading_dash", "-leading-dash"),
    ("spaces", "id with spaces"),
    ("percent_encoded_slash", "id%2Fslash"),
    ("one_over_the_limit", "a" * 129),
    ("newline", "line\nbreak"),
    ("tab", "id\ttab"),
    ("integer", 7),
    ("null", None),
    ("list", ["job-1"]),
)

RESERVED_JOB_IDS = (
    ("version_shaped", "job-1--20260911T070054Z"),
    ("trailing_word", "job--x"),
    ("bare_separator", "--"),
)

INVALID_TIMESTAMPS = (
    ("naive_no_zone", "2026-09-12T07:00:00"),
    ("bare_date", "2026-09-12"),
    ("junk", "not-a-date"),
    ("empty", ""),
    ("blank", "   "),
    ("number", 20260912),
    ("null", None),
    ("impossible_calendar_values", "2026-13-45T00:00:00Z"),
    ("impossible_offset", "2026-09-12T07:00:00+25:00"),
    ("trailing_junk", "2026-09-12T07:00:00ZZ"),
    ("out_of_range_year_one", "0001-01-01T00:00:00+10:00"),
    ("out_of_range_year_nine_thousand", "9999-12-31T23:59:59-10:00"),
)

VALID_TIMESTAMPS = (
    "2026-09-12T07:00:00Z",
    "2026-09-12T07:00:00+00:00",
    "2026-09-12T17:00:00+10:00",
    "2026-09-12T07:00:00.123456Z",
    "2026-09-11T20:00:00-05:00",
)

BLANK_OR_NON_STRING_VALUES = (
    ("empty", ""),
    ("blank", "   "),
    ("integer", 42),
    ("null", None),
    ("list", []),
    ("mapping", {}),
    ("boolean", True),
)

NON_MAPPING_DOCUMENT_VERSIONS = (
    ("list", ["resumes/job-1-v1.md"]),
    ("string", "resumes/job-1-v1.md"),
    ("integer", 1),
    ("boolean", True),
)


class ApplicationRecordContractTests(UpdateTrackerTestCase):
    def test_every_required_field_must_be_present(self):
        with self.sandbox() as box:
            for field in REQUIRED_FIELDS:
                with self.subTest(missing=field):
                    self.update_expecting_rejection(
                        box,
                        "required_field_missing",
                        field,
                        record=record_without(field),
                    )

    def test_a_blank_or_non_string_source_is_invalid(self):
        with self.sandbox() as box:
            for label, value in BLANK_OR_NON_STRING_VALUES:
                with self.subTest(source=label):
                    self.update_expecting_rejection(
                        box,
                        "required_field_invalid",
                        "source",
                        record=record_with(source=value),
                    )

    def test_unknown_top_level_keys_are_rejected(self):
        with self.sandbox() as box:
            self.update_expecting_rejection(
                box,
                "unrecognised_field_rejected",
                "recruiter_notes",
                record=record_with(recruiter_notes="call Tuesday"),
            )

    def test_every_unknown_top_level_key_is_named(self):
        with self.sandbox() as box:
            _result, report = self.update_expecting_rejection(
                box,
                "unrecognised_field_rejected",
                None,
                record=record_with(application_status="submitted", salary_aud=150000),
            )

        self.assertEqual(
            sorted(self.fields_for(report["errors"], "unrecognised_field_rejected")),
            ["application_status", "salary_aud"],
            "the retired application_status field must be refused by name, not "
            "silently absorbed into application_stage",
        )

    def test_document_versions_must_be_a_mapping(self):
        with self.sandbox() as box:
            for label, value in NON_MAPPING_DOCUMENT_VERSIONS:
                with self.subTest(document_versions=label):
                    self.update_expecting_rejection(
                        box,
                        "document_versions_invalid",
                        "document_versions",
                        record=record_with(document_versions=value),
                    )

    def test_a_minimal_record_is_accepted_and_creates_one_row(self):
        with self.sandbox() as box:
            report = self.update_ok(box)

            self.assertEqual(report["status"], "created")
            self.assertEqual(report["canonical_job_id"], "job-1")
            self.assertTrue(report["event_recorded"])
            self.assertEqual(self.row_ids(box), ["job-1"])
            self.assertEqual(len(self.events(box)), 1)

    def test_omitted_dimensions_take_their_documented_initial_value(self):
        with self.sandbox() as box:
            self.insert(box)

            row = self.row_for(box)
            event = self.events(box)[0]

        for dimension, initial in INITIAL_STATUS.items():
            with self.subTest(dimension=dimension):
                self.assertEqual(row[dimension], initial)
                self.assertEqual(event["to_status"][dimension], initial)
        self.assertEqual(row["application_stage"], "not_started")
        self.assertEqual(event["to_status"]["application_stage"], "not_started")

    def test_omitted_dimensions_are_unchanged_on_an_update(self):
        established = record_with(
            discovery_status="duplicate_of_known",
            verification_status="jd_verified",
            screening_status="eligible",
            action_status="follow_up",
        )
        with self.sandbox() as box:
            self.insert(box, record=established)

            report = self.update_ok(
                box,
                record=record_with(
                    event_type="status_changed",
                    application_stage="rejected",
                    occurred_at="2026-09-13T07:00:00Z",
                ),
            )

            self.assertEqual(report["status"], "updated")
            row = self.row_for(box)
            event = self.events(box)[-1]

        expected = {
            "discovery_status": "duplicate_of_known",
            "verification_status": "jd_verified",
            "screening_status": "eligible",
            "action_status": "follow_up",
            "application_stage": "rejected",
        }
        for dimension, value in expected.items():
            with self.subTest(dimension=dimension):
                self.assertEqual(row[dimension], value, "an omitted dimension changed")
                self.assertEqual(event["to_status"][dimension], value)
        self.assertEqual(
            event["from_status"],
            status_object(
                "not_started",
                discovery_status="duplicate_of_known",
                verification_status="jd_verified",
                screening_status="eligible",
                action_status="follow_up",
            ),
        )


class CanonicalJobIdSafetyTests(UpdateTrackerTestCase):
    def test_unsafe_canonical_job_ids_are_rejected_and_write_nothing(self):
        with self.sandbox() as box:
            for label, value in UNSAFE_JOB_IDS:
                with self.subTest(canonical_job_id=label):
                    self.update_expecting_rejection(
                        box,
                        "canonical_job_id_unsafe",
                        "canonical_job_id",
                        record=record_with(canonical_job_id=value),
                    )

    def test_identifiers_containing_the_version_separator_are_reserved(self):
        with self.sandbox() as box:
            for label, value in RESERVED_JOB_IDS:
                with self.subTest(reserved=label):
                    self.update_expecting_rejection(
                        box,
                        "canonical_job_id_reserved",
                        "canonical_job_id",
                        record=record_with(canonical_job_id=value),
                    )

    def test_safe_canonical_job_ids_are_accepted(self):
        with self.sandbox() as box:
            for job_id in ("job-1", "JOB-1", "a", "0", "job.id_1-2", "a" * 128):
                with self.subTest(canonical_job_id=job_id):
                    report = self.update_ok(
                        box, record=record_with(canonical_job_id=job_id)
                    )

                    self.assertEqual(report["status"], "created")
                    self.assertEqual(report["canonical_job_id"], job_id)
                    self.assertIn(job_id, self.row_ids(box))


class OccurredAtTests(UpdateTrackerTestCase):
    def test_occurred_at_must_be_an_rfc3339_instant_with_a_timezone(self):
        with self.sandbox() as box:
            for label, value in INVALID_TIMESTAMPS:
                with self.subTest(occurred_at=label):
                    self.update_expecting_rejection(
                        box,
                        "occurred_at_invalid",
                        "occurred_at",
                        record=record_with(occurred_at=value),
                    )

    def test_offset_and_zulu_instants_are_accepted(self):
        for value in VALID_TIMESTAMPS:
            with self.subTest(occurred_at=value):
                with self.sandbox() as box:
                    report = self.update_ok(box, record=record_with(occurred_at=value))

                    self.assertEqual(report["status"], "created")
                    self.assertEqual(self.events(box)[0]["occurred_at"], value)


class StatusVocabularyTests(UpdateTrackerTestCase):
    """Five dimensions, five vocabularies, five error codes.  A value that
    belongs to another dimension must be refused, because accepting it is
    exactly how five separate dimensions collapse back into one field."""

    def test_the_five_vocabularies_and_the_event_vocabulary_are_disjoint(self):
        for name, values in VOCABULARIES.items():
            with self.subTest(dimension=name):
                self.assertEqual(
                    len(set(values)), len(values), "duplicate value in a vocabulary"
                )
        for name, values in VOCABULARIES.items():
            for other_name, other_values in VOCABULARIES.items():
                if name >= other_name:
                    continue
                with self.subTest(pair=(name, other_name)):
                    self.assertEqual(
                        set(values) & set(other_values),
                        set(),
                        "two dimensions share a value, so a report cannot say which "
                        "dimension a status belongs to",
                    )
        # The vocabularies above are only a table until the script enforces
        # them, so the same claim is made operationally here: one value, one
        # owner.  ``eligible`` is a screening_status and nothing else, so
        # refusing it for application_stage is what disjointness means.
        with self.sandbox() as box:
            self.update_expecting_rejection(
                box,
                "application_stage_invalid",
                "application_stage",
                record=record_with(application_stage="eligible"),
            )

            report = self.update_ok(
                box, record=record_with(screening_status="eligible")
            )

            self.assertEqual(report["status"], "created")
            self.assertEqual(self.row_for(box)["screening_status"], "eligible")

    def test_a_value_from_another_dimension_is_rejected_for_this_one(self):
        # The matrix below only means anything because each of these values is
        # genuinely owned by the dimension it is listed under, so that is
        # established first.  event_type is left out of this half: it is not a
        # column of the row, only a field of the event.
        with self.sandbox() as box:
            for index, (owner, value) in enumerate(REPRESENTATIVE_VALUES.items()):
                if owner == "event_type":
                    continue
                with self.subTest(owner=owner, value=value):
                    self.update_ok(
                        box,
                        record=record_with(
                            occurred_at=f"2026-09-12T10:{index:02d}:00Z",
                            event_type="status_changed",
                            **{owner: value},
                        ),
                    )

                    self.assertEqual(self.row_for(box)[owner], value)

        with self.sandbox() as box:
            for dimension in VOCABULARIES:
                for other, value in REPRESENTATIVE_VALUES.items():
                    if other == dimension:
                        continue
                    with self.subTest(dimension=dimension, borrowed_from=other):
                        self.update_expecting_rejection(
                            box,
                            f"{dimension}_invalid",
                            dimension,
                            record=record_with(**{dimension: value}),
                        )

    def test_junk_values_are_rejected_for_every_dimension(self):
        with self.sandbox() as box:
            for dimension in VOCABULARIES:
                for label, value in (
                    ("junk", "not_a_real_status"),
                    ("empty", ""),
                    ("uppercased", REPRESENTATIVE_VALUES[dimension].upper()),
                    ("integer", 3),
                    ("null", None),
                    ("list", [REPRESENTATIVE_VALUES[dimension]]),
                ):
                    with self.subTest(dimension=dimension, value=label):
                        self.update_expecting_rejection(
                            box,
                            f"{dimension}_invalid",
                            dimension,
                            record=record_with(**{dimension: value}),
                        )

    def test_retired_values_are_refused_by_the_dimension_that_owned_them(self):
        """The approved vocabularies, and the proof the retired ones are gone.

        Pinning the three approved tuples here is documentation; feeding every
        retired value back to the dimension that used to accept it is the
        test.  A retired value that still validates means the old vocabulary
        is still in the script, and every report written against it means
        something different from what the Design Spec says it means.
        """
        self.assertEqual(
            SCREENING_STATUSES,
            ("new", "verified", "eligible", "excluded", "duplicate", "closed"),
        )
        self.assertEqual(
            APPLICATION_STAGES,
            (
                "not_started",
                "shortlisted",
                "preparing",
                "ready",
                "submitted",
                "interview",
                "offer",
                "accepted",
                "rejected",
                "withdrawn",
            ),
        )
        self.assertEqual(
            ACTION_STATUSES,
            (
                "none",
                "candidate_review",
                "document_edit",
                "application_form",
                "follow_up",
                "blocked",
            ),
        )
        for dimension, retired in RETIRED_VALUES:
            with self.subTest(table=dimension, retired=retired):
                self.assertNotIn(
                    retired,
                    VOCABULARIES[dimension],
                    "a retired value is still listed in a vocabulary",
                )

        with self.sandbox() as box:
            for dimension, retired in RETIRED_VALUES:
                with self.subTest(dimension=dimension, retired=retired):
                    self.update_expecting_rejection(
                        box,
                        f"{dimension}_invalid",
                        dimension,
                        record=record_with(**{dimension: retired}),
                    )

    def test_values_renamed_out_of_the_extra_dimensions_are_refused(self):
        """The approved three own ``duplicate``, ``verified`` and ``closed``.

        discovery_status and verification_status carried the first two and
        action_status carried the third.  The approved vocabularies are fixed,
        so the collisions were resolved on the other side, and each of these
        is now a value belonging to a different dimension: accepting one where
        it used to live is exactly how the dimensions stop being disjoint and
        a status stops being attributable to a dimension at all.
        """
        for dimension, renamed in RENAMED_AWAY_VALUES:
            with self.subTest(table=dimension, value=renamed):
                self.assertIn(renamed, SCREENING_STATUSES)
                self.assertNotIn(renamed, VOCABULARIES[dimension])

        with self.sandbox() as box:
            for dimension, renamed in RENAMED_AWAY_VALUES:
                with self.subTest(dimension=dimension, value=renamed):
                    self.update_expecting_rejection(
                        box,
                        f"{dimension}_invalid",
                        dimension,
                        record=record_with(**{dimension: renamed}),
                    )

    def assert_dimension_accepts_every_value(self, box, dimension, values):
        for index, value in enumerate(values):
            with self.subTest(**{dimension: value}):
                report = self.update_ok(
                    box,
                    record=record_with(
                        occurred_at=f"2026-09-12T07:{index:02d}:00Z",
                        event_type="status_changed",
                        **{dimension: value},
                    ),
                )

                self.assertEqual(
                    report["status"], "created" if index == 0 else "updated"
                )
                self.assertEqual(self.row_for(box)[dimension], value)
                self.assertEqual(self.events(box)[-1]["to_status"][dimension], value)

    def test_every_discovery_status_is_accepted(self):
        with self.sandbox() as box:
            self.assert_dimension_accepts_every_value(
                box, "discovery_status", DISCOVERY_STATUSES
            )

    def test_every_verification_status_is_accepted(self):
        with self.sandbox() as box:
            self.assert_dimension_accepts_every_value(
                box, "verification_status", VERIFICATION_STATUSES
            )

    def test_every_screening_status_is_accepted(self):
        with self.sandbox() as box:
            self.assert_dimension_accepts_every_value(
                box, "screening_status", SCREENING_STATUSES
            )

    def test_every_action_status_is_accepted(self):
        with self.sandbox() as box:
            self.assert_dimension_accepts_every_value(
                box, "action_status", ACTION_STATUSES
            )

    def test_every_application_stage_is_accepted_once_a_snapshot_is_linked(self):
        with self.sandbox() as box:
            snapshot_path = self.link_snapshot(box)
            for index, stage in enumerate(APPLICATION_STAGES):
                with self.subTest(application_stage=stage):
                    report = self.update_ok(
                        box,
                        record=record_with(
                            occurred_at=f"2026-09-12T08:{index:02d}:00Z",
                            event_type="status_changed",
                            application_stage=stage,
                            jd_snapshot_path=snapshot_path,
                        ),
                    )

                    self.assertEqual(
                        report["status"], "created" if index == 0 else "updated"
                    )
                    self.assertEqual(self.row_for(box)["application_stage"], stage)
                    self.assertEqual(
                        self.events(box)[-1]["to_status"]["application_stage"], stage
                    )

    def test_every_event_type_is_accepted_and_recorded(self):
        with self.sandbox() as box:
            for index, (event_type, action_status) in enumerate(
                zip(EVENT_TYPES, ACTION_STATUSES)
            ):
                with self.subTest(event_type=event_type):
                    self.update_ok(
                        box,
                        record=record_with(
                            occurred_at=f"2026-09-12T09:{index:02d}:00Z",
                            event_type=event_type,
                            action_status=action_status,
                        ),
                    )

                    self.assertEqual(self.events(box)[-1]["event_type"], event_type)


class SnapshotGateTests(UpdateTrackerTestCase):
    """A JD snapshot is the evidence a role was really read.  Every stage from
    shortlisting onward requires one; the early lifecycle and the two exits do
    not, so a role can be recorded and abandoned without inventing evidence."""

    def test_the_gate_partitions_the_ten_stages_into_six_gated_and_four_ungated(self):
        """Exactly six stages need evidence; the four the funnel does not run
        through do not, so a role can be recorded, rejected or abandoned
        without a snapshot being invented for it."""
        self.assertEqual(len(APPLICATION_STAGES), 10)
        self.assertEqual(
            GATED_STAGES,
            ("shortlisted", "preparing", "ready", "submitted", "interview", "offer"),
        )
        self.assertEqual(
            UNGATED_STAGES, ("not_started", "accepted", "rejected", "withdrawn")
        )
        self.assertEqual(
            set(GATED_STAGES) & set(UNGATED_STAGES),
            set(),
            "a stage cannot be both gated and ungated",
        )
        self.assertEqual(
            set(GATED_STAGES) | set(UNGATED_STAGES),
            set(APPLICATION_STAGES),
            "every approved stage must fall on one side of the gate",
        )
        # The partition above is only a table until the script enforces it.
        with self.sandbox() as box:
            self.update_expecting_rejection(
                box,
                "jd_snapshot_required",
                "jd_snapshot_path",
                record=record_with(application_stage="offer"),
            )

        with self.sandbox() as box:
            report = self.update_ok(
                box, record=record_with(application_stage="accepted")
            )

            self.assertEqual(report["status"], "created")
            self.assertIsNone(report["jd_snapshot_path"])

    def test_gated_stages_are_refused_without_a_snapshot(self):
        with self.sandbox() as box:
            for stage in GATED_STAGES:
                with self.subTest(application_stage=stage):
                    self.update_expecting_rejection(
                        box,
                        "jd_snapshot_required",
                        "jd_snapshot_path",
                        record=record_with(application_stage=stage),
                    )

    def test_ungated_stages_are_accepted_without_a_snapshot(self):
        for stage in UNGATED_STAGES:
            with self.subTest(application_stage=stage):
                with self.sandbox() as box:
                    report = self.update_ok(
                        box, record=record_with(application_stage=stage)
                    )

                    self.assertEqual(report["status"], "created")
                    self.assertIsNone(report["jd_snapshot_path"])
                    row = self.row_for(box)
                    self.assertEqual(row["application_stage"], stage)
                    self.assertEqual(row["jd_snapshot_path"], "")

    def test_an_early_lifecycle_insert_needs_no_snapshot(self):
        with self.sandbox() as box:
            report = self.update_ok(
                box, record=record_with(application_stage="not_started")
            )

            self.assertEqual(report["status"], "created")
            self.assertEqual(report["event_id"], self.events(box)[0]["event_id"])
            self.assertIsNone(self.events(box)[0]["jd_snapshot_path"])

    def test_reaching_a_gated_stage_from_a_snapshotless_row_is_refused(self):
        with self.sandbox() as box:
            self.insert(box)

            self.update_expecting_rejection(
                box,
                "jd_snapshot_required",
                "jd_snapshot_path",
                record=record_with(
                    event_type="status_changed",
                    application_stage="shortlisted",
                    occurred_at="2026-09-13T07:00:00Z",
                ),
            )

    def test_a_gated_stage_is_accepted_with_a_linked_snapshot(self):
        with self.sandbox() as box:
            snapshot_path = self.link_snapshot(box)

            report = self.update_ok(
                box,
                record=record_with(
                    application_stage="shortlisted", jd_snapshot_path=snapshot_path
                ),
            )

            self.assertEqual(report["status"], "created")
            self.assertEqual(report["jd_snapshot_path"], snapshot_path)

    def test_a_snapshot_linked_once_keeps_later_gated_stages_open(self):
        with self.sandbox() as box:
            snapshot_path = self.link_snapshot(box)
            self.insert(
                box,
                record=record_with(
                    application_stage="shortlisted", jd_snapshot_path=snapshot_path
                ),
            )

            report = self.update_ok(
                box,
                record=record_with(
                    event_type="status_changed",
                    application_stage="submitted",
                    occurred_at="2026-09-13T07:00:00Z",
                ),
            )

            self.assertEqual(report["status"], "updated")
            self.assertEqual(self.row_for(box)["jd_snapshot_path"], snapshot_path)


class FunnelTimestampTests(UpdateTrackerTestCase):
    """The four timestamp cells, and the one rule that governs all of them.

    ``first_seen_at`` is stamped when the row is inserted.  ``applied_at``,
    ``interview_at`` and ``offer_at`` are stamped when the row first reaches
    ``submitted``, ``interview`` or ``offer`` *or any later stage*: an
    application that is at ``offer`` has been submitted and interviewed
    whether or not anyone recorded those steps, so a jump straight to ``offer``
    stamps all three at that one instant.  Every cell takes the ``occurred_at``
    of the step that first reached it and is never written again, because the
    funnel is a record of when things happened, not of when they were typed in.

    ``rejected`` and ``withdrawn`` are exits, not stages of the funnel: they
    have no rank, so they conjure no timestamp out of nothing and erase none
    of the ones already there.
    """

    def funnel_cells(self, box, canonical_job_id="job-1"):
        row = self.row_for(box, canonical_job_id)
        return {column: row[column] for column in FUNNEL_COLUMNS}

    def assert_funnel(self, box, walk, canonical_job_id="job-1"):
        self.assertEqual(
            self.funnel_cells(box, canonical_job_id),
            expected_funnel(walk),
            f"the funnel cells do not match the walk {walk!r}",
        )

    # -- first_seen_at -----------------------------------------------------

    def test_first_seen_at_is_stamped_from_the_inserting_records_occurred_at(self):
        with self.sandbox() as box:
            self.insert(box, record=record_with(occurred_at="2026-09-12T04:05:06+10:00"))

            row = self.row_for(box)

        self.assertEqual(row["first_seen_at"], "2026-09-12T04:05:06+10:00")
        for column in FUNNEL_COLUMNS:
            with self.subTest(column=column):
                self.assertEqual(
                    row[column],
                    "",
                    "an insert at not_started has reached no part of the funnel",
                )

    def test_first_seen_at_survives_many_later_updates(self):
        walk = (
            "shortlisted",
            "preparing",
            "ready",
            "submitted",
            "interview",
            "offer",
            "accepted",
            "withdrawn",
        )
        with self.sandbox() as box:
            snapshot_path = self.link_snapshot(box)
            self.insert(box, record=record_with(occurred_at="2026-09-12T01:00:00Z"))
            first_seen_at = self.row_for(box)["first_seen_at"].encode("utf-8")
            self.assertEqual(first_seen_at, b"2026-09-12T01:00:00Z")

            for index, stage in enumerate(walk):
                with self.subTest(application_stage=stage):
                    self.update_ok(
                        box,
                        record=record_with(
                            event_type="status_changed",
                            application_stage=stage,
                            jd_snapshot_path=snapshot_path,
                            occurred_at=f"2026-09-{13 + index:02d}T07:00:00Z",
                        ),
                    )

                    self.assertEqual(
                        self.row_for(box)["first_seen_at"].encode("utf-8"),
                        first_seen_at,
                        "first_seen_at was overwritten by a later update",
                    )

    # -- the funnel proper -------------------------------------------------

    def test_the_ordinary_walk_stamps_each_funnel_cell_when_it_is_first_reached(self):
        walk = tuple(
            (stage, f"2026-09-12T{index + 1:02d}:00:00Z")
            for index, stage in enumerate(RANKED_STAGES)
        )
        with self.sandbox() as box:
            snapshot_path = self.link_snapshot(box)
            first_stage, first_occurred_at = walk[0]
            self.insert(
                box,
                record=record_with(
                    application_stage=first_stage,
                    occurred_at=first_occurred_at,
                    jd_snapshot_path=snapshot_path,
                ),
            )
            self.assert_funnel(box, walk[:1])

            for index, (stage, occurred_at) in enumerate(walk[1:], start=1):
                with self.subTest(application_stage=stage):
                    report = self.update_ok(
                        box,
                        record=record_with(
                            event_type="status_changed",
                            application_stage=stage,
                            occurred_at=occurred_at,
                        ),
                    )

                    self.assertEqual(report["status"], "updated")
                    self.assert_funnel(box, walk[: index + 1])

            row = self.row_for(box)

        self.assertEqual(row["first_seen_at"], "2026-09-12T01:00:00Z")
        self.assertEqual(row["applied_at"], "2026-09-12T05:00:00Z")
        self.assertEqual(row["interview_at"], "2026-09-12T06:00:00Z")
        self.assertEqual(row["offer_at"], "2026-09-12T07:00:00Z")

    def test_reaching_a_stage_stamps_every_funnel_column_it_passed(self):
        """One fresh job per stage, inserted straight at that stage.

        This is the "or later" rule stated as a table: the columns a stage
        stamps are exactly the ones whose own stage it has reached or passed,
        and the two rankless exits stamp nothing at all.
        """
        with self.sandbox() as box:
            for index, stage in enumerate((*RANKED_STAGES, *TERMINAL_STAGES)):
                with self.subTest(application_stage=stage):
                    canonical_job_id = f"job-{stage}"
                    snapshot_path = self.save_snapshot(
                        box,
                        job=job_with(canonical_job_id=canonical_job_id),
                        name=f"{canonical_job_id}.json",
                    )
                    occurred_at = f"2026-09-12T{index:02d}:30:00Z"

                    self.insert(
                        box,
                        record=record_with(
                            canonical_job_id=canonical_job_id,
                            application_stage=stage,
                            occurred_at=occurred_at,
                            jd_snapshot_path=snapshot_path,
                        ),
                    )

                    row = self.row_for(box, canonical_job_id)
                    self.assertEqual(row["first_seen_at"], occurred_at)
                    reached = funnel_columns_reached_by(stage)
                    for column in FUNNEL_COLUMNS:
                        self.assertEqual(
                            row[column],
                            occurred_at if column in reached else "",
                            f"{stage} stamped {column} wrongly",
                        )

    def test_a_jump_straight_to_offer_stamps_applied_interview_and_offer(self):
        with self.sandbox() as box:
            snapshot_path = self.link_snapshot(box)
            self.insert(box, record=record_with(occurred_at="2026-09-12T01:00:00Z"))
            self.assert_funnel(box, (("not_started", "2026-09-12T01:00:00Z"),))

            self.update_ok(
                box,
                record=record_with(
                    event_type="status_changed",
                    application_stage="offer",
                    jd_snapshot_path=snapshot_path,
                    occurred_at="2026-09-13T02:00:00Z",
                ),
            )

            row = self.row_for(box)

        self.assertEqual(row["first_seen_at"], "2026-09-12T01:00:00Z")
        for column in FUNNEL_COLUMNS:
            with self.subTest(column=column):
                self.assertEqual(
                    row[column],
                    "2026-09-13T02:00:00Z",
                    "a row at offer has been submitted and interviewed, so all "
                    "three cells are stamped at the instant it got there",
                )

    def test_a_jump_straight_to_accepted_stamps_all_three_funnel_columns(self):
        with self.sandbox() as box:
            self.insert(box, record=record_with(occurred_at="2026-09-12T01:00:00Z"))

            self.update_ok(
                box,
                record=record_with(
                    event_type="status_changed",
                    application_stage="accepted",
                    occurred_at="2026-09-14T03:00:00Z",
                ),
            )

            row = self.row_for(box)

        self.assertEqual(row["first_seen_at"], "2026-09-12T01:00:00Z")
        for column in FUNNEL_COLUMNS:
            with self.subTest(column=column):
                self.assertEqual(row[column], "2026-09-14T03:00:00Z")

    def test_existing_funnel_timestamps_are_never_overwritten(self):
        later_runs = (
            ("offer", "follow_up", "2026-09-15T04:00:00Z"),
            ("offer", "blocked", "2026-09-16T05:00:00Z"),
            ("accepted", "none", "2026-09-17T06:00:00Z"),
        )
        with self.sandbox() as box:
            snapshot_path = self.link_snapshot(box)
            self.insert(
                box,
                record=record_with(
                    application_stage="submitted",
                    jd_snapshot_path=snapshot_path,
                    occurred_at="2026-09-12T01:00:00Z",
                ),
            )
            for stage, occurred_at in (
                ("interview", "2026-09-13T02:00:00Z"),
                ("offer", "2026-09-14T03:00:00Z"),
            ):
                self.update_ok(
                    box,
                    record=record_with(
                        event_type="status_changed",
                        application_stage=stage,
                        occurred_at=occurred_at,
                    ),
                )

            established = {
                column: self.row_for(box)[column]
                for column in ("first_seen_at", *FUNNEL_COLUMNS)
            }
            self.assertEqual(
                established,
                {
                    "first_seen_at": "2026-09-12T01:00:00Z",
                    "applied_at": "2026-09-12T01:00:00Z",
                    "interview_at": "2026-09-13T02:00:00Z",
                    "offer_at": "2026-09-14T03:00:00Z",
                },
            )

            for stage, action_status, occurred_at in later_runs:
                with self.subTest(occurred_at=occurred_at):
                    self.update_ok(
                        box,
                        record=record_with(
                            event_type="status_changed",
                            application_stage=stage,
                            action_status=action_status,
                            occurred_at=occurred_at,
                        ),
                    )

                    row = self.row_for(box)
                    for column, value in established.items():
                        self.assertEqual(
                            row[column].encode("utf-8"),
                            value.encode("utf-8"),
                            f"{column} was rewritten by a later update carrying a "
                            "different occurred_at",
                        )

    # -- the two exits -----------------------------------------------------

    def test_rejected_from_an_early_stage_conjures_no_funnel_timestamps(self):
        with self.sandbox() as box:
            self.insert(box, record=record_with(occurred_at="2026-09-12T01:00:00Z"))

            self.update_ok(
                box,
                record=record_with(
                    event_type="status_changed",
                    application_stage="rejected",
                    occurred_at="2026-09-13T02:00:00Z",
                ),
            )

            row = self.row_for(box)

        self.assertEqual(row["first_seen_at"], "2026-09-12T01:00:00Z")
        self.assertEqual(row["application_stage"], "rejected")
        for column in FUNNEL_COLUMNS:
            with self.subTest(column=column):
                self.assertEqual(
                    row[column],
                    "",
                    "a role rejected before it was ever submitted has no funnel "
                    "timestamps to invent",
                )

    def test_withdrawn_after_interview_keeps_what_was_reached_and_invents_nothing(self):
        with self.sandbox() as box:
            snapshot_path = self.link_snapshot(box)
            self.insert(
                box,
                record=record_with(
                    application_stage="submitted",
                    jd_snapshot_path=snapshot_path,
                    occurred_at="2026-09-12T01:00:00Z",
                ),
            )
            self.update_ok(
                box,
                record=record_with(
                    event_type="status_changed",
                    application_stage="interview",
                    occurred_at="2026-09-13T02:00:00Z",
                ),
            )

            self.update_ok(
                box,
                record=record_with(
                    event_type="status_changed",
                    application_stage="withdrawn",
                    occurred_at="2026-09-14T03:00:00Z",
                ),
            )

            row = self.row_for(box)

        self.assertEqual(row["application_stage"], "withdrawn")
        self.assertEqual(row["first_seen_at"], "2026-09-12T01:00:00Z")
        self.assertEqual(row["applied_at"], "2026-09-12T01:00:00Z")
        self.assertEqual(row["interview_at"], "2026-09-13T02:00:00Z")
        self.assertEqual(
            row["offer_at"],
            "",
            "withdrawing is not reaching an offer",
        )


def split_snapshot(text):
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError("snapshot must open with a standalone YAML delimiter line")
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            frontmatter = yaml.safe_load("".join(lines[1:index]))
            return frontmatter, "".join(lines[index + 1 :])
    raise ValueError("snapshot must close its frontmatter with a standalone delimiter")


def rerender_snapshot(frontmatter, body):
    """Re-render a real snapshot's own frontmatter and body, so a planted
    defect is the only difference from something Task 5 wrote."""
    rendered = yaml.safe_dump(
        frontmatter, sort_keys=True, allow_unicode=True, default_flow_style=False
    )
    return "---\n" + rendered + "---\n" + body


MALFORMED_SNAPSHOT_BODIES = (
    ("no_frontmatter_at_all", "Just an advertisement with no frontmatter.\n"),
    ("unterminated_frontmatter", "---\ncanonical_job_id: job-1\nstill going\n"),
    ("unparseable_yaml", "---\ncanonical_job_id: [unclosed\n---\nbody\n"),
    ("frontmatter_is_a_list", "---\n- job-1\n- job-2\n---\nbody\n"),
    ("empty_file", ""),
)


class SnapshotPathIntegrityTests(UpdateTrackerTestCase):
    """The tracker stores the snapshot path Task 5 returned, verbatim.  A
    regenerated or normalised path can drift from the archived bytes it is
    supposed to cite, and traceability is the whole point of the snapshot."""

    def linked_record(self, snapshot_path, **overrides):
        return record_with(
            application_stage="shortlisted",
            jd_snapshot_path=snapshot_path,
            **overrides,
        )

    def test_the_path_task_five_returned_is_stored_byte_for_byte(self):
        with self.sandbox() as box:
            snapshot_path = self.save_snapshot(box)
            self.assertEqual(snapshot_path, BASE_SNAPSHOT_PATH)

            report = self.update_ok(box, record=self.linked_record(snapshot_path))

            self.assertEqual(report["jd_snapshot_path"], snapshot_path)
            stored = self.row_for(box)["jd_snapshot_path"]
            recorded = self.events(box)[-1]["jd_snapshot_path"]

        self.assertEqual(
            stored.encode("utf-8"),
            snapshot_path.encode("utf-8"),
            "the CSV cell must be the exact string Task 5 reported",
        )
        self.assertEqual(
            recorded.encode("utf-8"),
            snapshot_path.encode("utf-8"),
            "the event field must be the exact string Task 5 reported",
        )

    def test_non_canonical_spellings_of_a_real_snapshot_are_refused(self):
        with self.sandbox() as box:
            self.save_snapshot(box)
            spellings = (
                ("leading_dot_slash", "./jobs/snapshots/job-1.md"),
                ("doubled_separator", "jobs//snapshots/job-1.md"),
                ("round_trip_through_parent", "jobs/snapshots/../snapshots/job-1.md"),
                ("interior_dot_segment", "jobs/./snapshots/job-1.md"),
                ("backslash_separators", "jobs\\snapshots\\job-1.md"),
                ("trailing_slash", "jobs/snapshots/job-1.md/"),
                ("surrounding_whitespace", "  jobs/snapshots/job-1.md  "),
                ("absolute", str(box.workspace.resolve() / "jobs/snapshots/job-1.md")),
            )
            for label, spelling in spellings:
                with self.subTest(spelling=label):
                    self.update_expecting_rejection(
                        box,
                        "jd_snapshot_path_not_canonical",
                        "jd_snapshot_path",
                        record=self.linked_record(spelling),
                    )

    def test_a_non_string_snapshot_path_is_refused(self):
        with self.sandbox() as box:
            self.save_snapshot(box)
            for label, value in (("integer", 7), ("null", None), ("list", []), ("empty", "")):
                with self.subTest(jd_snapshot_path=label):
                    self.update_expecting_rejection(
                        box,
                        "jd_snapshot_path_not_canonical",
                        "jd_snapshot_path",
                        record=self.linked_record(value),
                    )

    def test_a_snapshot_that_does_not_exist_is_refused(self):
        with self.sandbox() as box:
            self.save_snapshot(box)

            self.update_expecting_rejection(
                box,
                "jd_snapshot_missing",
                "jd_snapshot_path",
                record=self.linked_record("jobs/snapshots/no-such-job.md"),
            )

    def test_a_directory_at_the_snapshot_path_is_refused(self):
        # Which of the three snapshot codes fits a directory is arbitrary, so
        # only the refusal itself is pinned: a non-regular file must never be
        # accepted as evidence, and must never crash the run.
        refusals = {
            "jd_snapshot_missing",
            "jd_snapshot_invalid",
            "jd_snapshot_path_unsafe",
        }
        with self.sandbox() as box:
            (box.snapshots / "job-1.md").mkdir(parents=True)
            record_path = self.write_record(
                box, record=self.linked_record(BASE_SNAPSHOT_PATH)
            )
            before = snapshot_tree(box.root)

            result, report = self.update(box, record_path=record_path)

            self.assertEqual(snapshot_tree(box.root), before)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(report["status"], "rejected")
            self.assertTrue(
                set(self.codes(report["errors"])) & refusals,
                f"errors were {report['errors']}",
            )
            self.assertFalse(report["event_recorded"])

    def rewrite_snapshot(self, box, text):
        (box.snapshots / "job-1.md").write_text(text, encoding="utf-8")

    def test_a_re_rendered_real_snapshot_is_still_accepted(self):
        # The control for the two loops below.  Both start from a genuine
        # Task 5 snapshot and re-render it, so the only difference between
        # this accepted file and those rejected ones is the planted defect.
        with self.sandbox() as box:
            snapshot_path = self.save_snapshot(box)
            frontmatter, body = split_snapshot(
                (box.workspace / snapshot_path).read_text(encoding="utf-8")
            )
            self.rewrite_snapshot(box, rerender_snapshot(frontmatter, body))

            report = self.update_ok(box, record=self.linked_record(snapshot_path))

            self.assertEqual(report["status"], "created")

    def test_an_unreadable_frontmatter_is_not_a_legitimate_snapshot(self):
        with self.sandbox() as box:
            self.save_snapshot(box)
            for label, contents in MALFORMED_SNAPSHOT_BODIES:
                with self.subTest(snapshot=label):
                    self.rewrite_snapshot(box, contents)

                    self.update_expecting_rejection(
                        box,
                        "jd_snapshot_invalid",
                        "jd_snapshot_path",
                        record=self.linked_record(BASE_SNAPSHOT_PATH),
                    )

    def test_missing_required_frontmatter_keys_are_not_a_legitimate_snapshot(self):
        with self.sandbox() as box:
            snapshot_path = self.save_snapshot(box)
            complete, body = split_snapshot(
                (box.workspace / snapshot_path).read_text(encoding="utf-8")
            )
            self.assertEqual(set(complete), set(SNAPSHOT_FRONTMATTER_KEYS))
            for key in SNAPSHOT_FRONTMATTER_KEYS:
                with self.subTest(missing_frontmatter_key=key):
                    frontmatter = {
                        name: value
                        for name, value in complete.items()
                        if name != key
                    }
                    self.rewrite_snapshot(
                        box, rerender_snapshot(frontmatter, body)
                    )

                    self.update_expecting_rejection(
                        box,
                        "jd_snapshot_invalid",
                        "jd_snapshot_path",
                        record=self.linked_record(BASE_SNAPSHOT_PATH),
                    )

    def test_a_snapshot_belonging_to_another_job_is_refused(self):
        with self.sandbox() as box:
            other = self.save_snapshot(
                box,
                job=job_with(canonical_job_id="other-job"),
                name="other-job.json",
            )
            self.assertEqual(other, "jobs/snapshots/other-job.md")

            self.update_expecting_rejection(
                box,
                "jd_snapshot_invalid",
                "jd_snapshot_path",
                record=self.linked_record(other),
            )


class SnapshotContainmentTests(UpdateTrackerTestCase):
    """Containment is decided by resolution plus a per-component symlink walk
    (Task 3's ``validate_contained_paths``), never by a string prefix.  The
    user's symlink is evidence of what they did: it is refused, not followed,
    deleted or replaced."""

    def linked_record(self, snapshot_path=BASE_SNAPSHOT_PATH):
        return record_with(
            application_stage="shortlisted", jd_snapshot_path=snapshot_path
        )

    def plant_snapshot(self, directory, contents, name="job-1.md"):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(contents)

    def replace_with_directory_symlink(self, path, target):
        shutil.rmtree(path)
        path.symlink_to(target, target_is_directory=True)
        self.assertTrue(path.is_symlink())

    def assert_symlink_intact(self, link, target):
        self.assertTrue(link.is_symlink(), "the user's symlink was removed or replaced")
        self.assertEqual(
            os.readlink(link), str(target), "the user's symlink was re-pointed"
        )

    def test_a_jobs_directory_symlink_pointing_outside_is_refused(self):
        with self.sandbox() as box:
            contents = self.snapshot_bytes(box)
            target = box.outside / "stolen-jobs"
            self.plant_snapshot(target / "snapshots", contents)
            link = box.workspace / "jobs"
            self.replace_with_directory_symlink(link, target)

            self.update_expecting_rejection(
                box, "jd_snapshot_path_unsafe", "jd_snapshot_path",
                record=self.linked_record(),
            )

            self.assert_symlink_intact(link, target)

    def test_a_snapshots_directory_symlink_pointing_outside_is_refused(self):
        with self.sandbox() as box:
            contents = self.snapshot_bytes(box)
            target = box.outside / "stolen-snapshots"
            self.plant_snapshot(target, contents)
            link = box.snapshots
            self.replace_with_directory_symlink(link, target)

            self.update_expecting_rejection(
                box, "jd_snapshot_path_unsafe", "jd_snapshot_path",
                record=self.linked_record(),
            )

            self.assert_symlink_intact(link, target)

    def test_a_snapshots_directory_symlink_pointing_inside_is_refused(self):
        with self.sandbox() as box:
            contents = self.snapshot_bytes(box)
            target = box.workspace / "reports"
            self.plant_snapshot(target, contents)
            link = box.snapshots
            self.replace_with_directory_symlink(link, target)

            self.update_expecting_rejection(
                box, "jd_snapshot_path_unsafe", "jd_snapshot_path",
                record=self.linked_record(),
            )

            self.assert_symlink_intact(link, target)

    def test_a_symlink_at_the_snapshot_file_itself_is_refused(self):
        with self.sandbox() as box:
            target = box.outside / "leak.md"
            target.write_bytes(self.snapshot_bytes(box))
            link = box.snapshots / "job-1.md"
            link.unlink()
            link.symlink_to(target)

            self.update_expecting_rejection(
                box, "jd_snapshot_path_unsafe", "jd_snapshot_path",
                record=self.linked_record(),
            )

            self.assert_symlink_intact(link, target)

    def test_a_sibling_sharing_the_workspace_name_prefix_is_still_outside(self):
        with self.sandbox() as box:
            # ".../private-workspace-evil/snapshots" starts with
            # ".../private-workspace", so a string-prefix containment check
            # would call it contained.  The per-component symlink walk refuses
            # the planted link before the resolution comparison is reached.
            contents = self.snapshot_bytes(box)
            sibling = box.root / (box.workspace.name + "-evil")
            target = sibling / "snapshots"
            self.plant_snapshot(target, contents)
            self.assertTrue(str(target).startswith(str(box.workspace)))
            link = box.snapshots
            self.replace_with_directory_symlink(link, target)

            self.update_expecting_rejection(
                box, "jd_snapshot_path_unsafe", "jd_snapshot_path",
                record=self.linked_record(),
            )

            self.assert_symlink_intact(link, target)

    def test_a_relative_path_escaping_the_workspace_is_refused(self):
        # The only symlink-free escape a record can spell.  Whether it is
        # caught as a non-canonical spelling or as an escape is an
        # implementation choice; that it is refused, and that the file outside
        # is neither read into the tracker nor written, is not.
        refusals = {"jd_snapshot_path_unsafe", "jd_snapshot_path_not_canonical"}
        with self.sandbox() as box:
            target = box.outside / "leak.md"
            target.write_bytes(self.snapshot_bytes(box))
            record_path = self.write_record(
                box,
                record=self.linked_record("../outside-private-data/leak.md"),
            )
            before = snapshot_tree(box.root)

            result, report = self.update(box, record_path=record_path)

            self.assertEqual(snapshot_tree(box.root), before)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(report["status"], "rejected")
            self.assertTrue(
                set(self.codes(report["errors"])) & refusals,
                f"errors were {report['errors']}",
            )
            self.assertFalse(report["event_recorded"])


class WorkspaceContainmentTests(UpdateTrackerTestCase):
    def test_workspace_must_exist(self):
        with self.sandbox() as box:
            self.update_expecting_rejection(
                box,
                "workspace_unsafe",
                "workspace",
                workspace=box.root / "no-such-workspace",
            )

    def test_workspace_must_be_a_directory(self):
        with self.sandbox() as box:
            plain_file = box.root / "not-a-directory"
            plain_file.write_text("x\n", encoding="utf-8")

            self.update_expecting_rejection(
                box, "workspace_unsafe", "workspace", workspace=plain_file
            )

    def test_workspace_must_not_be_a_symlink(self):
        with self.sandbox() as box:
            link = box.root / "workspace-link"
            link.symlink_to(box.workspace, target_is_directory=True)

            self.update_expecting_rejection(
                box, "workspace_unsafe", "workspace", workspace=link
            )

            self.assertTrue(link.is_symlink(), "the user's symlink was replaced")
            self.assertEqual(os.readlink(link), str(box.workspace))

    def test_workspace_must_not_be_the_skill_repository(self):
        with self.sandbox() as box:
            self.update_expecting_rejection(
                box, "workspace_unsafe", "workspace", workspace=ROOT
            )

        self.assertFalse((ROOT / "tracker").exists(), "the repository must stay clean")
        self.assertFalse((ROOT / "jobs").exists(), "the repository must stay clean")

    def test_workspace_must_not_be_inside_the_skill_repository(self):
        inside = ROOT / "tests"

        with self.sandbox() as box:
            self.update_expecting_rejection(
                box, "workspace_unsafe", "workspace", workspace=inside
            )

        self.assertFalse((inside / "tracker").exists(), "the repository must stay clean")


class ApplicationsCsvTests(UpdateTrackerTestCase):
    """The CSV is current state: one row per job, the agreed 17 columns, and a
    replacement written by rename so a reader never sees a half-written file."""

    def raw_header(self, box):
        return box.applications_csv.read_text(encoding="utf-8").splitlines()[0]

    def insert_three_jobs(self, box):
        for index, job_id in enumerate(("job-a", "job-b", "job-c")):
            self.insert(
                box,
                record=record_with(
                    canonical_job_id=job_id,
                    occurred_at=f"2026-09-12T07:0{index}:00Z",
                ),
            )

    def test_the_header_is_the_agreed_seventeen_columns_in_that_order(self):
        with self.sandbox() as box:
            self.insert(box)

            self.assertEqual(self.raw_header(box), ",".join(APPLICATION_HEADER))
            self.assertEqual(len(APPLICATION_HEADER), 17)

    def test_an_update_replaces_the_row_rather_than_appending_a_second(self):
        with self.sandbox() as box:
            self.insert(box)

            for index, stage in enumerate(("rejected", "not_started")):
                report = self.update_ok(
                    box,
                    record=record_with(
                        event_type="status_changed",
                        application_stage=stage,
                        occurred_at=f"2026-09-13T07:0{index}:00Z",
                    ),
                )
                self.assertEqual(report["status"], "updated")

            self.assertEqual(self.row_ids(box), ["job-1"])
            self.assertEqual(len(self.csv_rows(box)), 1)

    def test_existing_row_order_is_preserved_and_a_new_job_appends_at_the_end(self):
        with self.sandbox() as box:
            self.insert_three_jobs(box)
            self.assertEqual(self.row_ids(box), ["job-a", "job-b", "job-c"])

            self.update_ok(
                box,
                record=record_with(
                    canonical_job_id="job-a",
                    event_type="status_changed",
                    application_stage="rejected",
                    occurred_at="2026-09-13T07:00:00Z",
                ),
            )

            self.assertEqual(
                self.row_ids(box),
                ["job-a", "job-b", "job-c"],
                "an update must rewrite a row where it stands, not move it",
            )

            self.insert(
                box,
                record=record_with(
                    canonical_job_id="job-d", occurred_at="2026-09-13T08:00:00Z"
                ),
            )

            self.assertEqual(
                self.row_ids(box), ["job-a", "job-b", "job-c", "job-d"]
            )

    def test_duplicate_rows_for_one_job_are_refused_rather_than_disambiguated(self):
        with self.sandbox() as box:
            self.insert(box)
            lines = box.applications_csv.read_text(encoding="utf-8").splitlines(True)
            self.assertEqual(len(lines), 2)
            with box.applications_csv.open("a", encoding="utf-8", newline="") as handle:
                handle.write(lines[1])

            self.update_expecting_rejection(
                box,
                "tracker_duplicate_row",
                "canonical_job_id",
                record=record_with(
                    event_type="status_changed",
                    application_stage="rejected",
                    occurred_at="2026-09-13T07:00:00Z",
                ),
            )

    def test_descriptive_columns_are_populated_from_the_linked_snapshot(self):
        with self.sandbox() as box:
            snapshot_path = self.link_snapshot(box)

            self.insert(
                box,
                record=record_with(
                    source="record_board",
                    application_stage="shortlisted",
                    jd_snapshot_path=snapshot_path,
                ),
            )

            row = self.row_for(box)

        for column in DESCRIPTIVE_COLUMNS:
            with self.subTest(column=column):
                self.assertEqual(
                    row[column],
                    SNAPSHOT_JOB[column],
                    "descriptive columns describe the advertisement, so they come "
                    "from the snapshot's frontmatter, not from the record",
                )

    def test_descriptive_columns_are_left_as_they_are_without_a_snapshot(self):
        with self.sandbox() as box:
            self.insert(box, record=record_with(source="record_board"))
            first = self.row_for(box)
            for column in DESCRIPTIVE_COLUMNS:
                with self.subTest(column=column, run="insert"):
                    self.assertEqual(first[column], "")

            self.update_ok(
                box,
                record=record_with(
                    source="another_board",
                    event_type="status_changed",
                    application_stage="rejected",
                    occurred_at="2026-09-13T07:00:00Z",
                ),
            )
            second = self.row_for(box)
            for column in DESCRIPTIVE_COLUMNS:
                with self.subTest(column=column, run="update"):
                    self.assertEqual(second[column], "")

            snapshot_path = self.link_snapshot(box)
            self.update_ok(
                box,
                record=record_with(
                    source="a_third_board",
                    event_type="snapshot_linked",
                    application_stage="shortlisted",
                    jd_snapshot_path=snapshot_path,
                    occurred_at="2026-09-13T08:00:00Z",
                ),
            )

            self.assertEqual(self.row_for(box)["source"], SNAPSHOT_JOB["source"])

    def test_an_update_publishes_a_fresh_file_by_rename(self):
        with self.sandbox() as box:
            self.insert(box)
            original_inode = box.applications_csv.stat().st_ino

            self.update_ok(
                box,
                record=record_with(
                    event_type="status_changed",
                    application_stage="rejected",
                    occurred_at="2026-09-13T07:00:00Z",
                ),
            )

            self.assertNotEqual(
                box.applications_csv.stat().st_ino,
                original_inode,
                "an update must rename a completed temporary file into place, so a "
                "reader never sees a half-written tracker",
            )

    @unittest.skipIf(RUNNING_AS_ROOT, "root bypasses file permissions")
    def test_an_unwritable_tracker_directory_leaves_both_files_untouched(self):
        with self.sandbox() as box:
            self.insert(box)
            applications = box.applications_csv.read_bytes()
            events = box.events_jsonl.read_bytes()
            self.pin_mtime(box.applications_csv)
            self.pin_mtime(box.events_jsonl)
            record_path = self.write_record(
                box,
                record=record_with(
                    event_type="status_changed",
                    application_stage="rejected",
                    occurred_at="2026-09-13T07:00:00Z",
                ),
            )
            os.chmod(box.tracker, 0o500)
            try:
                result, report = self.update(box, record_path=record_path)

                self.assert_rejected(result, report, "tracker_write_failed", None)
                self.assertFalse(
                    report["event_recorded"],
                    "the CSV write is proved first, so a failure there must happen "
                    "before any event is appended",
                )
                self.assert_untouched(box.applications_csv, applications)
                self.assert_untouched(box.events_jsonl, events)
                self.assertEqual(
                    sorted(path.name for path in box.tracker.iterdir()),
                    ["applications.csv", "events.jsonl"],
                    "no temporary file and no stray dotfile may be left behind",
                )
            finally:
                os.chmod(box.tracker, 0o700)


class EventLogTests(UpdateTrackerTestCase):
    """events.jsonl is history: one JSON object per line, appended, never
    rewritten.  A rewritten history cannot be replayed against, and replay is
    the only recovery there is when the two files disagree."""

    def test_each_state_change_appends_exactly_one_well_formed_event(self):
        with self.sandbox() as box:
            self.insert(box)
            self.assertEqual(len(self.events(box)), 1)

            self.update_ok(
                box,
                record=record_with(
                    event_type="status_changed",
                    application_stage="rejected",
                    occurred_at="2026-09-13T07:00:00Z",
                ),
            )

            events = self.events(box)
            self.assertEqual(len(events), 2)
            for event in events:
                self.assert_event_shape(event)
            self.assertEqual(
                box.events_jsonl.read_text(encoding="utf-8").count("\n"),
                2,
                "each event occupies exactly one newline-terminated line",
            )

    def test_the_report_names_the_event_it_appended(self):
        with self.sandbox() as box:
            report = self.insert(box)

            self.assertTrue(report["event_recorded"])
            self.assertEqual(report["event_id"], self.events(box)[-1]["event_id"])

    def test_from_status_is_null_on_the_first_insert(self):
        with self.sandbox() as box:
            self.insert(box)

            event = self.events(box)[0]
            self.assertIsNone(event["from_status"])
            self.assertEqual(event["to_status"], status_object("not_started"))

    def test_from_status_carries_the_previous_five_dimensions(self):
        with self.sandbox() as box:
            self.insert(box)

            self.update_ok(
                box,
                record=record_with(
                    event_type="status_changed",
                    application_stage="rejected",
                    action_status="blocked",
                    occurred_at="2026-09-13T07:00:00Z",
                ),
            )

            event = self.events(box)[-1]
            self.assertEqual(event["from_status"], status_object("not_started"))
            self.assertEqual(
                event["to_status"],
                status_object("rejected", action_status="blocked"),
            )

    def test_event_ids_are_unique_across_jobs_and_runs(self):
        with self.sandbox() as box:
            for index, job_id in enumerate(("job-a", "job-b")):
                self.insert(
                    box,
                    record=record_with(
                        canonical_job_id=job_id,
                        occurred_at=f"2026-09-12T07:0{index}:00Z",
                    ),
                )
            for index, job_id in enumerate(("job-a", "job-b")):
                self.update_ok(
                    box,
                    record=record_with(
                        canonical_job_id=job_id,
                        event_type="status_changed",
                        application_stage="rejected",
                        occurred_at=f"2026-09-13T07:0{index}:00Z",
                    ),
                )

            identifiers = [event["event_id"] for event in self.events(box)]

        self.assertEqual(len(identifiers), 4)
        self.assertEqual(len(set(identifiers)), 4, "event_id must be unique")

    def test_the_log_only_ever_grows_and_earlier_lines_stay_byte_identical(self):
        with self.sandbox() as box:
            self.insert(box)
            before = box.events_jsonl.read_bytes()
            inode = box.events_jsonl.stat().st_ino

            for index, stage in enumerate(("rejected", "not_started")):
                self.update_ok(
                    box,
                    record=record_with(
                        event_type="status_changed",
                        application_stage=stage,
                        occurred_at=f"2026-09-13T07:0{index}:00Z",
                    ),
                )

            after = box.events_jsonl.read_bytes()

            self.assertTrue(
                after.startswith(before),
                "an earlier event was rewritten; the log is append-only",
            )
            self.assertGreater(len(after), len(before))
            self.assertEqual(
                box.events_jsonl.stat().st_ino,
                inode,
                "the log must be appended to in place, never replaced wholesale",
            )
            self.assertEqual(len(self.events(box)), 3)

    def test_a_rejected_run_appends_nothing(self):
        with self.sandbox() as box:
            self.insert(box)
            before = box.events_jsonl.read_bytes()

            self.update_expecting_rejection(
                box,
                "application_stage_invalid",
                "application_stage",
                record=record_with(application_stage="excluded"),
            )

            self.assertEqual(box.events_jsonl.read_bytes(), before)

    def test_a_no_op_writes_neither_file_and_records_no_event(self):
        with self.sandbox() as box:
            self.insert(box)
            applications = box.applications_csv.read_bytes()
            events = box.events_jsonl.read_bytes()
            self.pin_mtime(box.applications_csv)
            self.pin_mtime(box.events_jsonl)

            for label, record in (
                ("identical_record", record_with()),
                (
                    "later_occurrence_of_the_same_state",
                    record_with(
                        event_type="status_changed",
                        occurred_at="2026-09-13T07:00:00Z",
                    ),
                ),
            ):
                with self.subTest(no_op=label):
                    report = self.update_ok(box, record=record)

                    self.assertEqual(report["status"], "unchanged")
                    self.assertFalse(report["event_recorded"])
                    self.assertIsNone(report["event_id"])
                    self.assert_untouched(box.applications_csv, applications)
                    self.assert_untouched(box.events_jsonl, events)
                    self.assertEqual(len(self.events(box)), 1)

    def test_document_versions_default_to_an_empty_object(self):
        with self.sandbox() as box:
            self.insert(box)

            self.assertEqual(self.events(box)[-1]["document_versions"], {})

    def test_document_versions_are_recorded_as_supplied(self):
        versions = {
            "resume": "resumes/job-1-v3.md",
            "cover_letter": "cover-letters/job-1-v2.md",
        }
        with self.sandbox() as box:
            self.insert(
                box,
                record=record_with(
                    event_type="documents_updated", document_versions=versions
                ),
            )

            self.assertEqual(self.events(box)[-1]["document_versions"], versions)

    def test_the_event_source_is_the_records_source(self):
        with self.sandbox() as box:
            self.insert(box, record=record_with(source="record_board"))

            self.assertEqual(self.events(box)[-1]["source"], "record_board")


def planted_event(canonical_job_id, to_status, **overrides):
    """An event line written by hand, to stage a log the tracker must read."""
    event = {
        "event_id": "planted-0000",
        "occurred_at": "2026-09-12T06:00:00Z",
        "canonical_job_id": canonical_job_id,
        "event_type": "status_changed",
        "from_status": None,
        "to_status": to_status,
        "jd_snapshot_path": None,
        "document_versions": {},
        "source": "example_board",
    }
    event.update(overrides)
    return json.dumps(event) + "\n"


WRAPPER_SOURCE = '''\
import os
import pathlib
import sys

sys.path.insert(0, os.environ["TRACKER_SCRIPTS_DIRECTORY"])

import update_tracker as tracker


{hook}

tracker.main()
'''

FAIL_THE_PUBLISH_HOOK = '''\
def refuse_to_publish(*arguments, **keywords):
    raise OSError("the tracker publish step was interrupted")


pathlib.Path.replace = refuse_to_publish
os.replace = refuse_to_publish
os.rename = refuse_to_publish
'''

FAIL_THE_EVENT_APPEND_HOOK = '''\
_open_a_file = os.open


def refuse_the_event_append(path, flags, *arguments, **keywords):
    if str(path).endswith("events.jsonl"):
        raise OSError("the tracker event append was interrupted")
    return _open_a_file(path, flags, *arguments, **keywords)


os.open = refuse_the_event_append
'''

CHANGED_RECORD = record_with(
    event_type="status_changed",
    application_stage="rejected",
    occurred_at="2026-09-13T07:00:00Z",
)

SECOND_CHANGE_RECORD = record_with(
    event_type="status_changed",
    application_stage="withdrawn",
    occurred_at="2026-09-14T07:00:00Z",
)


class TransactionBoundaryTests(UpdateTrackerTestCase):
    """The run touches two files, so it cannot be atomic.  The order is
    chosen so the surviving failure is the recoverable one: prove the CSV
    write, make the event durable, then publish the state.  A state change
    that lost its event is unrecoverable; an event whose state never published
    is detected by the precheck and can be replayed.
    """

    def run_interposed(
        self, box, hook, record=MISSING, timeout=SUBPROCESS_TIMEOUT_SECONDS
    ):
        # The wrapper is written into the sandbox, never into the repository.
        wrapper = box.root / "interposed-tracker.py"
        wrapper.write_text(WRAPPER_SOURCE.format(hook=hook), encoding="utf-8")
        record_path = self.write_record(box, record=record)
        variables = dict(os.environ)
        variables["TRACKER_SCRIPTS_DIRECTORY"] = str(SCRIPT.parent)
        variables["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            result = subprocess.run(
                [
                    "python3",
                    str(wrapper),
                    "--workspace",
                    str(box.workspace),
                    "--input-json",
                    str(record_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                env=variables,
            )
        except subprocess.TimeoutExpired:
            self.fail(f"the interposed run did not finish within {timeout}s")
        return result, self.parse_report(result, secrets=box.secrets())

    def test_a_csv_row_that_disagrees_with_the_last_event_fails_closed(self):
        with self.sandbox() as box:
            self.insert(box)
            with box.events_jsonl.open("a", encoding="utf-8") as handle:
                handle.write(planted_event("job-1", status_object("shortlisted")))

            self.update_expecting_rejection(
                box, "tracker_out_of_sync", None, record=CHANGED_RECORD
            )

    def test_another_jobs_divergence_does_not_block_this_job(self):
        with self.sandbox() as box:
            self.insert(box)
            with box.events_jsonl.open("a", encoding="utf-8") as handle:
                handle.write(planted_event("job-2", status_object("submitted")))

            report = self.update_ok(box, record=CHANGED_RECORD)

            self.assertEqual(report["status"], "updated")
            self.assertEqual(self.row_ids(box), ["job-1"])

    def test_a_publish_failure_reports_the_event_and_leaves_the_state_alone(self):
        with self.sandbox() as box:
            self.insert(box)
            applications = box.applications_csv.read_bytes()
            self.pin_mtime(box.applications_csv)
            events_before = box.events_jsonl.read_bytes()

            result, report = self.run_interposed(
                box, FAIL_THE_PUBLISH_HOOK, record=CHANGED_RECORD
            )

            self.assert_rejected(result, report, "tracker_publish_failed", None)
            self.assertTrue(
                report["event_recorded"],
                "the caller must be told the event landed, because that is what "
                "makes the interrupted change replayable",
            )
            events_after = box.events_jsonl.read_bytes()
            self.assertTrue(events_after.startswith(events_before))
            self.assertEqual(len(self.events(box)), 2)
            self.assertEqual(self.events(box)[-1]["event_id"], report["event_id"])
            self.assert_untouched(box.applications_csv, applications)
            self.assertEqual(
                sorted(path.name for path in box.tracker.iterdir()),
                ["applications.csv", "events.jsonl"],
                "the unpublished temporary file must not be left behind",
            )

            # The two files now disagree, so the next run must refuse rather
            # than layer a second change on divergent state.
            self.update_expecting_rejection(
                box, "tracker_out_of_sync", None, record=SECOND_CHANGE_RECORD
            )

            self.assert_untouched(box.applications_csv, applications)
            self.assertEqual(box.events_jsonl.read_bytes(), events_after)

    def test_a_publish_failure_is_never_reported_as_success(self):
        with self.sandbox() as box:
            self.insert(box)

            result, report = self.run_interposed(
                box, FAIL_THE_PUBLISH_HOOK, record=CHANGED_RECORD
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(report["status"], "rejected")
            self.assertNotEqual(report["errors"], [])
            self.assertEqual(
                self.row_for(box)["application_stage"],
                "not_started",
                "a run that wrote only one of the two files must never report the "
                "new state as published",
            )


    @unittest.skipIf(RUNNING_AS_ROOT, "root bypasses file permissions")
    def test_a_failed_event_append_publishes_nothing_and_claims_no_event(self):
        """The failure the whole ordering exists to survive.

        The replacement CSV has already been written and fsynced when the
        append fails, so this is the moment the run is holding a complete new
        state it must throw away: the event is what makes a change replayable,
        and a state published without one is unrecoverable.  Both files must
        be byte-identical afterwards and nothing may be left in the tracker
        directory -- and because the two files never diverged, the next
        ordinary run must succeed rather than be blocked by the precheck.
        """
        with self.sandbox() as box:
            self.insert(box)
            applications = box.applications_csv.read_bytes()
            events = box.events_jsonl.read_bytes()
            self.pin_mtime(box.applications_csv)
            self.pin_mtime(box.events_jsonl)
            record_path = self.write_record(box, record=CHANGED_RECORD)
            os.chmod(box.events_jsonl, 0o400)
            try:
                result, report = self.update(box, record_path=record_path)

                self.assertEqual(result.returncode, 1)
                self.assert_rejected(
                    result, report, "tracker_event_write_failed", None
                )
                self.assertFalse(
                    report["event_recorded"],
                    "no event landed, so the report must not claim one",
                )
                self.assertIsNone(report["event_id"])
                self.assert_untouched(box.applications_csv, applications)
                self.assert_untouched(box.events_jsonl, events)
                self.assertEqual(
                    sorted(path.name for path in box.tracker.iterdir()),
                    ["applications.csv", "events.jsonl"],
                    "the staged temporary file must not be left behind",
                )
            finally:
                os.chmod(box.events_jsonl, 0o600)

            report = self.update_ok(box, record=CHANGED_RECORD)

            self.assertEqual(
                report["status"],
                "updated",
                "the two files never diverged, so the precheck must not block the "
                "next ordinary run",
            )
            self.assertTrue(report["event_recorded"])
            self.assertEqual(len(self.events(box)), 2)
            self.assertEqual(self.row_for(box)["application_stage"], "rejected")

    def test_an_interrupted_event_append_leaves_the_tracker_exactly_as_it_was(self):
        """The same failure reached by interposing on the append rather than by
        file permissions, so it stays pinned where permissions do not bite."""
        with self.sandbox() as box:
            self.insert(box)
            applications = box.applications_csv.read_bytes()
            events = box.events_jsonl.read_bytes()
            self.pin_mtime(box.applications_csv)
            self.pin_mtime(box.events_jsonl)

            result, report = self.run_interposed(
                box, FAIL_THE_EVENT_APPEND_HOOK, record=CHANGED_RECORD
            )

            self.assert_rejected(result, report, "tracker_event_write_failed", None)
            self.assertFalse(report["event_recorded"])
            self.assertIsNone(report["event_id"])
            self.assert_untouched(box.applications_csv, applications)
            self.assert_untouched(box.events_jsonl, events)
            self.assertEqual(
                sorted(path.name for path in box.tracker.iterdir()),
                ["applications.csv", "events.jsonl"],
                "the staged temporary file must not be left behind",
            )


    def test_an_unencodable_record_is_reported_rather_than_crashing_the_run(self):
        """The last-resort arm of the two-file write.

        ``json.loads`` accepts an escaped lone surrogate; encoding the event
        line that would carry it does not, and a UnicodeEncodeError is not an
        OSError, so it escapes every diagnostic the write path names and lands
        in the guard around the whole run.  The staged CSV replacement has
        already been written by then, so this is the same "throw the new state
        away" obligation as a failed append: one JSON report on stdout, exit 1,
        no event claimed, nothing left in the tracker directory, and both
        files byte-identical.
        """
        unencodable = (
            '{"canonical_job_id": "job-1", "occurred_at": "2026-09-13T07:00:00Z",'
            ' "event_type": "status_changed", "source": "\\ud800",'
            ' "application_stage": "rejected"}'
        )
        with self.sandbox() as box:
            self.insert(box)
            applications = box.applications_csv.read_bytes()
            events = box.events_jsonl.read_bytes()
            self.pin_mtime(box.applications_csv)
            self.pin_mtime(box.events_jsonl)

            result, report = self.update(box, raw=unencodable)

            self.assert_rejected(
                result, report, "tracker_update_failed", "application"
            )
            self.assertFalse(report["event_recorded"])
            self.assertIsNone(report["event_id"])
            self.assert_untouched(box.applications_csv, applications)
            self.assert_untouched(box.events_jsonl, events)
            self.assertEqual(
                sorted(path.name for path in box.tracker.iterdir()),
                ["applications.csv", "events.jsonl"],
                "the staged temporary file must not be left behind",
            )


class TrackerReadabilityTests(UpdateTrackerTestCase):
    """A tracker file this skill cannot read as it writes them stops the run.

    The read is bounded and typed before it happens, because an unbounded read
    of whatever is on disk can be killed for memory and a killed process
    prints no report at all.  Every one of these refusals happens in the
    precheck, before a single byte is planned, so both files must survive
    untouched.
    """

    OVERSIZE_BYTES = 4 * 1024 * 1024 + 1

    @unittest.skipIf(RUNNING_AS_ROOT, "root bypasses file permissions")
    def test_an_unreadable_event_log_is_refused(self):
        with self.sandbox() as box:
            self.insert(box)
            os.chmod(box.events_jsonl, 0o000)
            try:
                self.update_expecting_rejection(
                    box, "tracker_unreadable", "tracker", record=CHANGED_RECORD
                )
            finally:
                os.chmod(box.events_jsonl, 0o600)

    @unittest.skipIf(RUNNING_AS_ROOT, "root bypasses file permissions")
    def test_an_unreadable_applications_csv_is_refused(self):
        with self.sandbox() as box:
            self.insert(box)
            os.chmod(box.applications_csv, 0o000)
            try:
                self.update_expecting_rejection(
                    box, "tracker_unreadable", "tracker", record=CHANGED_RECORD
                )
            finally:
                os.chmod(box.applications_csv, 0o600)

    def test_an_oversize_event_log_is_refused_rather_than_read(self):
        with self.sandbox() as box:
            self.insert(box)
            padding = self.OVERSIZE_BYTES - box.events_jsonl.stat().st_size
            self.assertGreater(padding, 0)
            with box.events_jsonl.open("a", encoding="utf-8") as handle:
                handle.write("\n" * padding)
            self.assertEqual(box.events_jsonl.stat().st_size, self.OVERSIZE_BYTES)

            self.update_expecting_rejection(
                box, "tracker_unreadable", "tracker", record=CHANGED_RECORD
            )

    def test_an_event_log_line_that_is_not_an_event_is_refused(self):
        with self.sandbox() as box:
            self.insert(box)
            for label, line in (
                ("not_json", "this line is not an event\n"),
                ("json_scalar", "7\n"),
                ("json_list", '["job-1"]\n'),
            ):
                with self.subTest(line=label):
                    original = box.events_jsonl.read_bytes()
                    with box.events_jsonl.open("a", encoding="utf-8") as handle:
                        handle.write(line)
                    try:
                        self.update_expecting_rejection(
                            box,
                            "tracker_unreadable",
                            "tracker",
                            record=CHANGED_RECORD,
                        )
                    finally:
                        box.events_jsonl.write_bytes(original)


def raw_with_duplicate_key(field, first, second):
    """A JSON object that names one key twice, spelled by hand because no
    encoder will produce it."""
    remaining = {key: value for key, value in VALID_RECORD.items() if key != field}
    items = [f"{json.dumps(key)}: {json.dumps(value)}" for key, value in remaining.items()]
    items.append(f"{json.dumps(field)}: {json.dumps(first)}")
    items.append(f"{json.dumps(field)}: {json.dumps(second)}")
    return "{" + ", ".join(items) + "}"


class RecordRobustnessTests(UpdateTrackerTestCase):
    def test_a_missing_record_file_is_reported(self):
        with self.sandbox() as box:
            self.update_expecting_rejection(
                box,
                "application_record_unreadable",
                "application",
                record_path=box.inbox / "no-such-record.json",
            )

    def test_a_directory_as_the_record_file_is_reported(self):
        with self.sandbox() as box:
            directory = box.inbox / "record-directory"
            directory.mkdir()

            self.update_expecting_rejection(
                box,
                "application_record_unreadable",
                "application",
                record_path=directory,
                timeout=NON_REGULAR_TIMEOUT_SECONDS,
            )

    def test_a_fifo_as_the_record_file_is_reported_without_hanging(self):
        with self.sandbox() as box:
            fifo = box.inbox / "record-fifo"
            os.mkfifo(fifo)
            try:
                result = self.run_tracker(
                    "--workspace",
                    box.workspace,
                    "--input-json",
                    fifo,
                    timeout=NON_REGULAR_TIMEOUT_SECONDS,
                )
            finally:
                fifo.unlink()

            report = self.parse_report(result, secrets=box.secrets())
            self.assert_rejected(
                result, report, "application_record_unreadable", "application"
            )

    @unittest.skipIf(RUNNING_AS_ROOT, "root bypasses file permissions")
    def test_an_unreadable_record_file_is_reported(self):
        with self.sandbox() as box:
            record_path = self.write_record(box)
            os.chmod(record_path, 0o000)
            try:
                self.update_expecting_rejection(
                    box,
                    "application_record_unreadable",
                    "application",
                    record_path=record_path,
                )
            finally:
                os.chmod(record_path, 0o600)

    def test_malformed_json_is_reported(self):
        with self.sandbox() as box:
            for label, raw in (
                ("truncated", '{"canonical_job_id": "job-1"'),
                ("trailing_comma", '{"canonical_job_id": "job-1",}'),
                ("not_json", "canonical_job_id: job-1\n"),
                ("empty", ""),
            ):
                with self.subTest(record=label):
                    self.update_expecting_rejection(
                        box, "application_record_malformed", "application", raw=raw
                    )

    def test_json_that_is_not_a_mapping_is_reported(self):
        with self.sandbox() as box:
            for label, raw in (
                ("list", '[{"canonical_job_id": "job-1"}]'),
                ("string", '"job-1"'),
                ("number", "7"),
                ("null", "null"),
                ("boolean", "true"),
            ):
                with self.subTest(record=label):
                    self.update_expecting_rejection(
                        box,
                        "application_record_not_a_mapping",
                        "application",
                        raw=raw,
                    )

    def test_both_arguments_are_required(self):
        with self.sandbox() as box:
            record_path = self.write_record(box)
            invocations = (
                ("no_arguments", ()),
                ("workspace_only", ("--workspace", box.workspace)),
                ("input_json_only", ("--input-json", record_path)),
            )

            for label, arguments in invocations:
                with self.subTest(invocation=label):
                    result = self.run_tracker(*arguments)

                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("required", result.stderr.lower())
                    self.assertNotIn("traceback", result.stderr.lower())

    def test_a_duplicated_key_in_the_record_is_last_wins(self):
        # A known, accepted risk of json.loads: the last spelling of a repeated
        # key silently wins.  Pinned as current behaviour so a future change to
        # it is visible, not because it is desirable.
        with self.sandbox() as box:
            self.update_expecting_rejection(
                box,
                "application_stage_invalid",
                "application_stage",
                raw=raw_with_duplicate_key(
                    "application_stage", "not_started", "not_a_stage"
                ),
            )

        with self.sandbox() as box:
            report = self.update_ok(
                box,
                raw=raw_with_duplicate_key(
                    "application_stage", "not_a_stage", "not_started"
                ),
            )

            self.assertEqual(report["status"], "created")
            self.assertEqual(self.row_for(box)["application_stage"], "not_started")


class FixtureTests(UpdateTrackerTestCase):
    """The two fixtures must keep composing: Task 5 saves the advertisement,
    Task 6 records the application against the snapshot Task 5 returned."""

    def load(self, path):
        self.assertTrue(path.is_file(), f"missing fixture: {path.name}")
        return json.loads(path.read_text(encoding="utf-8"))

    def assert_fixture_is_a_complete_synthetic_record(self, record, job):
        self.assertIsInstance(record, dict)
        for field in REQUIRED_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, record)
        self.assertEqual(
            record["canonical_job_id"],
            job["canonical_job_id"],
            "the two fixtures must describe the same job so they compose",
        )
        for dimension, vocabulary in VOCABULARIES.items():
            if dimension in record:
                with self.subTest(dimension=dimension):
                    self.assertIn(
                        record[dimension],
                        vocabulary,
                        "the fixture must spell this dimension with the approved "
                        "vocabulary, not a retired value",
                    )
        self.assertIn(record["application_stage"], APPLICATION_STAGES)
        self.assertIn(record["event_type"], EVENT_TYPES)
        self.assertEqual(
            record["jd_snapshot_path"],
            f"jobs/snapshots/{job['canonical_job_id']}.md",
            "the fixture must cite the path Task 5 mints for the job fixture",
        )
        self.assertIsInstance(record["document_versions"], dict)

    def test_the_two_fixtures_compose_end_to_end_in_a_fresh_workspace(self):
        job = self.load(JOB_FIXTURE)
        record = self.load(APPLICATION_FIXTURE)
        self.assert_fixture_is_a_complete_synthetic_record(record, job)

        with self.sandbox() as box:
            snapshot_path = self.save_snapshot(
                box, job=job, name="fixture-job.json"
            )
            self.assertEqual(snapshot_path, record["jd_snapshot_path"])

            report = self.update_ok(box, record=record)

            self.assertEqual(report["status"], "created")
            self.assertEqual(report["canonical_job_id"], job["canonical_job_id"])
            self.assertEqual(report["jd_snapshot_path"], snapshot_path)
            self.assertTrue(report["event_recorded"])

            row = self.row_for(box, job["canonical_job_id"])
            self.assertEqual(row["jd_snapshot_path"], snapshot_path)
            self.assertEqual(row["application_stage"], record["application_stage"])
            self.assertEqual(row["screening_status"], record["screening_status"])
            self.assertEqual(row["action_status"], record["action_status"])
            for column in DESCRIPTIVE_COLUMNS:
                with self.subTest(column=column):
                    self.assertEqual(row[column], job[column])

            event = self.events(box)[-1]
            self.assert_event_shape(event)
            self.assertIsNone(event["from_status"])
            self.assertEqual(event["jd_snapshot_path"], snapshot_path)
            self.assertEqual(event["document_versions"], record["document_versions"])
            self.assertEqual(event["occurred_at"], record["occurred_at"])


if __name__ == "__main__":
    unittest.main()
