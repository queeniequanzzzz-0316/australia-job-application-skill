import contextlib
import csv
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import tempfile
import unittest

import yaml

try:
    import resource
except ImportError:  # pragma: no cover - POSIX only
    resource = None


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "save_job_snapshot.py"
INITIALISER = ROOT / "scripts" / "initialise_workspace.py"
FIXTURE = ROOT / "tests" / "fixtures" / "sample-job.json"

REPORT_KEYS = {
    "status",
    "jd_snapshot_path",
    "canonical_job_id",
    "content_digest",
    "errors",
}
DIAGNOSTIC_KEYS = {"code", "field", "message"}
STATUSES = {"created", "unchanged", "updated", "versioned", "rejected"}

REQUIRED_FIELDS = (
    "canonical_job_id",
    "retrieved_at",
    "last_verified_at",
    "source",
    "canonical_url",
    "job_title",
    "company",
    "location",
    "job_description",
)

FRONTMATTER_KEYS = {
    "canonical_job_id",
    "retrieved_at",
    "last_verified_at",
    "source",
    "canonical_url",
    "job_title",
    "company",
    "location",
    "content_digest",
}

MATERIAL_FIELDS = (
    "job_description",
    "source",
    "canonical_url",
    "job_title",
    "company",
    "location",
)

PROVENANCE_ONLY_FIELDS = ("retrieved_at", "last_verified_at")

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
    "application_status",
    "first_seen_at",
    "last_verified_at",
    "applied_at",
    "interview_at",
    "offer_at",
]

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

SUBPROCESS_TIMEOUT_SECONDS = 60
NON_REGULAR_TIMEOUT_SECONDS = 10
PINNED_MTIME_NS = 1600000000000000000

RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

# A snapshot comfortably larger than the file-size limit installed below, so
# the write fails only after mkstemp has already created the temporary file.
WRITABLE_FILE_LIMIT_BYTES = 8 * 1024
OVERSIZE_DESCRIPTION = "A long Australian advertisement paragraph.\n" * 4000


def limit_written_file_size():  # pragma: no cover - runs in the child
    import signal

    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(
        resource.RLIMIT_FSIZE,
        (WRITABLE_FILE_LIMIT_BYTES, WRITABLE_FILE_LIMIT_BYTES),
    )

MISSING = object()

# A job description that exercises every byte class the snapshot must preserve:
# markdown, a standalone "---" line, CJK, emoji, a line with trailing spaces and
# no final newline at all.
RICH_DESCRIPTION = (
    "# Backend Engineer\n"
    "\n"
    "---\n"
    "\n"
    "* Build **reliable** payment services.   \n"
    "* \u56e2\u961f\u534f\u4f5c is valued here.\n"
    "* Ship with confidence \U0001f680 \U0001f1e6\U0001f1fa\n"
    "\n"
    "> Salary: AUD 150,000 + super\n"
    "\n"
    "---\n"
    "Apply now.  "
)

VALID_JOB = {
    "canonical_job_id": "job-1",
    "retrieved_at": "2026-09-11T07:00:54Z",
    "last_verified_at": "2026-09-11T07:00:54Z",
    "source": "example_board",
    "canonical_url": "https://example.com/jobs/1042",
    "job_title": "Backend Engineer",
    "company": "Southern Cross Analytics Pty Ltd",
    "location": "Melbourne VIC 3000, Australia",
    "job_description": "We are hiring a backend engineer in Melbourne.\n",
}


def job_with(**overrides):
    job = dict(VALID_JOB)
    job.update(overrides)
    return job


def job_without(*keys):
    job = dict(VALID_JOB)
    for key in keys:
        job.pop(key, None)
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


def split_snapshot(text):
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ValueError("snapshot must open with a standalone YAML delimiter line")
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            frontmatter = yaml.safe_load("".join(lines[1:index]))
            return frontmatter, "".join(lines[index + 1 :])
    raise ValueError("snapshot must close its frontmatter with a standalone delimiter")


class Sandbox:
    """A temporary parent holding the workspace, a sibling that must stay
    untouched, and an inbox for job records."""

    def __init__(self, root):
        self.root = root
        self.workspace = root / "private-workspace"
        self.outside = root / "outside-private-data"
        self.inbox = root / "inbox"

    @property
    def snapshots(self):
        return self.workspace / "jobs" / "snapshots"

    @property
    def applications_csv(self):
        return self.workspace / "tracker" / "applications.csv"

    @property
    def events_jsonl(self):
        return self.workspace / "tracker" / "events.jsonl"

    def secrets(self):
        return (
            str(self.root),
            str(self.workspace),
            str(self.outside),
            str(self.inbox),
            self.workspace.name,
            self.outside.name,
        )


class SaveJobSnapshotTestCase(unittest.TestCase):
    maxDiff = None

    # -- process plumbing --------------------------------------------------

    def run_saver(self, *arguments, timeout=SUBPROCESS_TIMEOUT_SECONDS):
        try:
            return subprocess.run(
                ["python3", str(SCRIPT), *map(str, arguments)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            self.fail(
                f"save_job_snapshot did not finish within {timeout}s for arguments "
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

    def write_job(self, box, job=MISSING, raw=None, name="job-record.json"):
        if raw is None:
            raw = json.dumps(VALID_JOB if job is MISSING else job, ensure_ascii=False)
        path = box.inbox / name
        path.write_text(raw, encoding="utf-8")
        return path

    # -- report shape ------------------------------------------------------

    def parse_report(self, result, secrets=()):
        stdout = result.stdout
        try:
            report = json.loads(stdout)
        except ValueError:
            self.fail(
                "save_job_snapshot must print exactly one JSON object on stdout; got "
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
        if rejected:
            self.assertIsNone(report["jd_snapshot_path"])
        else:
            self.assertIsInstance(report["jd_snapshot_path"], str)
            self.assertIsInstance(report["canonical_job_id"], str)
            self.assertRegex(report["content_digest"], DIGEST_PATTERN)
            self.assertFalse(
                os.path.isabs(report["jd_snapshot_path"]),
                "jd_snapshot_path must be workspace-relative",
            )
            self.assertNotIn("\\", report["jd_snapshot_path"])
            self.assertFalse(report["jd_snapshot_path"].startswith("/"))
        if report["canonical_job_id"] is not None:
            self.assertIsInstance(report["canonical_job_id"], str)
        if report["content_digest"] is not None:
            self.assertRegex(report["content_digest"], DIGEST_PATTERN)
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

    def save(
        self,
        box,
        job=MISSING,
        raw=None,
        workspace=MISSING,
        job_path=None,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    ):
        if job_path is None:
            job_path = self.write_job(box, job=job, raw=raw)
        workspace_argument = box.workspace if workspace is MISSING else workspace
        result = self.run_saver(
            "--workspace", workspace_argument, "--job", job_path, timeout=timeout
        )
        report = self.parse_report(
            result, secrets=(*box.secrets(), str(job_path), str(workspace_argument))
        )
        return result, report

    def save_ok(self, box, job=MISSING, **kwargs):
        result, report = self.save(box, job=job, **kwargs)
        self.assertEqual(report["errors"], [], f"unexpected errors: {report['errors']}")
        self.assertEqual(result.returncode, 0)
        return report

    def assert_rejected(self, result, report, code, field):
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["status"], "rejected")
        self.assertIsNone(report["jd_snapshot_path"])
        self.assertIn(
            code, self.codes(report["errors"]), f"errors were {report['errors']}"
        )
        if field is not None:
            self.assertEqual(self.fields_for(report["errors"], code), [field])

    def save_expecting_rejection(
        self,
        box,
        code,
        field,
        job=MISSING,
        raw=None,
        workspace=MISSING,
        job_path=None,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    ):
        if job_path is None:
            job_path = self.write_job(box, job=job, raw=raw)
        before = snapshot_tree(box.root)

        result, report = self.save(
            box, job_path=job_path, workspace=workspace, timeout=timeout
        )

        self.assertEqual(
            snapshot_tree(box.root),
            before,
            "a rejected run must not create, change or delete a single byte anywhere",
        )
        self.assert_rejected(result, report, code, field)
        return result, report

    # -- snapshot helpers --------------------------------------------------

    def snapshot_names(self, box):
        return sorted(path.name for path in box.snapshots.iterdir())

    def snapshot_file(self, box, report):
        return box.workspace / report["jd_snapshot_path"]

    def read_snapshot(self, path):
        return split_snapshot(path.read_text(encoding="utf-8"))

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

    # -- tracker references ------------------------------------------------

    def reference_in_applications_csv(self, box, snapshot_path, job_id="job-1"):
        row = {name: "" for name in APPLICATION_HEADER}
        row["canonical_job_id"] = job_id
        row["jd_snapshot_path"] = snapshot_path
        with box.applications_csv.open("a", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, APPLICATION_HEADER).writerow(row)

    def reference_in_events_jsonl(self, box, snapshot_path, job_id="job-1"):
        event = {
            "event": "jd_snapshot_recorded",
            "canonical_job_id": job_id,
            "jd_snapshot_path": snapshot_path,
            "recorded_at": "2026-09-11T07:05:00Z",
        }
        with box.events_jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")


UNSAFE_JOB_IDS = (
    ("parent_escape", "../escape"),
    ("dot_dot", ".."),
    ("dot", "."),
    ("embedded_parent", "a/../b"),
    ("absolute", "/absolute/id"),
    ("nested_segment", "jobs/x"),
    ("backslash", "a\\b"),
    ("windows_drive", "C:\\x"),
    ("home_relative", "~/x"),
    ("home", "~"),
    ("empty", ""),
    ("blank", "   "),
    ("hidden_dotfile", ".hidden"),
    ("leading_dot", ".leading-dot"),
    ("leading_dash", "-leading-dash"),
    ("spaces", "id with spaces"),
    ("percent_encoded_slash", "id%2Fslash"),
    ("nul_byte", "id\u0000nul"),
    ("two_hundred_characters", "a" * 200),
    ("one_over_the_limit", "a" * 129),
    ("cjk", "\u804c\u4f4d-1"),
    ("latin_one_accent", "caf\u00e9"),
    ("fullwidth_solidus", "a\uff0fb"),
    ("newline", "line\nbreak"),
    ("carriage_return", "line\rbreak"),
    ("tab", "id\ttab"),
    ("integer", 7),
    ("null", None),
    ("list", ["job-1"]),
)

SAFE_JOB_IDS = (
    "job-1",
    "JOB-1",
    "a",
    "0",
    "A9",
    "job.id_1-2",
    "a" * 128,
)

INVALID_TIMESTAMPS = (
    ("naive_no_zone", "2026-09-11T07:00:54"),
    ("bare_date", "2026-09-11"),
    ("not_a_date", "not-a-date"),
    ("empty", ""),
    ("blank", "   "),
    ("number", 20260911),
    ("impossible_calendar_values", "2026-13-45T00:00:00Z"),
    ("impossible_offset", "2026-09-11T07:00:54+25:00"),
    ("trailing_junk", "2026-09-11T07:00:54ZZ"),
)

VALID_TIMESTAMPS = (
    "2026-09-11T07:00:54Z",
    "2026-09-11T07:00:54+00:00",
    "2026-09-11T17:00:54+10:00",
    "2026-09-11T07:00:54.123456Z",
    "2026-09-10T20:00:54-05:00",
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

TEXT_FIELDS = (
    "source",
    "canonical_url",
    "job_title",
    "company",
    "location",
    "job_description",
)


class JobRecordContractTests(SaveJobSnapshotTestCase):
    def test_every_required_field_must_be_present(self):
        for field in REQUIRED_FIELDS:
            with self.subTest(missing=field):
                with self.sandbox() as box:
                    self.save_expecting_rejection(
                        box,
                        "required_field_missing",
                        field,
                        job=job_without(field),
                    )

    def test_blank_or_non_string_text_fields_are_invalid(self):
        for field in TEXT_FIELDS:
            for label, value in BLANK_OR_NON_STRING_VALUES:
                with self.subTest(field=field, value=label):
                    with self.sandbox() as box:
                        self.save_expecting_rejection(
                            box,
                            "required_field_invalid",
                            field,
                            job=job_with(**{field: value}),
                        )

    def test_unknown_top_level_keys_are_rejected(self):
        # Task 4 already publishes "unrecognised_field" as a *warning* that
        # keeps the value. Here an unknown key is a hard error, so it needs its
        # own code or a consumer keying on codes across both reports is misled.
        with self.sandbox() as box:
            self.save_expecting_rejection(
                box,
                "unrecognised_field_rejected",
                "salary_aud",
                job=job_with(salary_aud=150000),
            )

    def test_every_unknown_top_level_key_is_named(self):
        with self.sandbox() as box:
            _result, report = self.save_expecting_rejection(
                box,
                "unrecognised_field_rejected",
                None,
                job=job_with(salary_aud=150000, recruiter_notes="call Tuesday"),
            )

        self.assertEqual(
            sorted(self.fields_for(report["errors"], "unrecognised_field_rejected")),
            ["recruiter_notes", "salary_aud"],
        )

    def test_a_complete_record_is_accepted(self):
        with self.sandbox() as box:
            report = self.save_ok(box)

        self.assertEqual(report["status"], "created")
        self.assertEqual(report["canonical_job_id"], "job-1")
        self.assertEqual(report["jd_snapshot_path"], "jobs/snapshots/job-1.md")


class CanonicalJobIdSafetyTests(SaveJobSnapshotTestCase):
    def test_unsafe_canonical_job_ids_are_rejected_and_write_nothing(self):
        for label, value in UNSAFE_JOB_IDS:
            with self.subTest(canonical_job_id=label):
                with self.sandbox() as box:
                    self.save_expecting_rejection(
                        box,
                        "canonical_job_id_unsafe",
                        "canonical_job_id",
                        job=job_with(canonical_job_id=value),
                    )
                    self.assertEqual(self.snapshot_names(box), [])

    def test_safe_canonical_job_ids_are_accepted(self):
        for job_id in SAFE_JOB_IDS:
            with self.subTest(canonical_job_id=job_id):
                with self.sandbox() as box:
                    report = self.save_ok(box, job=job_with(canonical_job_id=job_id))

                    self.assertEqual(report["canonical_job_id"], job_id)
                    self.assertEqual(
                        report["jd_snapshot_path"], f"jobs/snapshots/{job_id}.md"
                    )
                    self.assertEqual(self.snapshot_names(box), [f"{job_id}.md"])

    def test_one_hundred_and_twenty_eight_characters_is_the_length_boundary(self):
        with self.sandbox() as box:
            report = self.save_ok(box, job=job_with(canonical_job_id="b" * 128))
            self.assertEqual(report["jd_snapshot_path"], "jobs/snapshots/" + "b" * 128 + ".md")

        with self.sandbox() as box:
            self.save_expecting_rejection(
                box,
                "canonical_job_id_unsafe",
                "canonical_job_id",
                job=job_with(canonical_job_id="b" * 129),
            )

    def test_case_differing_id_is_an_alias_conflict_not_a_silent_overwrite(self):
        with self.sandbox() as box:
            created = self.save_ok(box, job=job_with(canonical_job_id="job-1"))
            base = self.snapshot_file(box, created)
            original = base.read_bytes()
            self.pin_mtime(base)

            job_path = self.write_job(
                box,
                job=job_with(canonical_job_id="JOB-1", job_title="Backend Engineer II"),
                name="aliased-job-record.json",
            )
            before = snapshot_tree(box.root)

            result, report = self.save(box, job_path=job_path)

            self.assertEqual(snapshot_tree(box.root), before)
            self.assert_rejected(
                result, report, "canonical_job_id_alias_conflict", "canonical_job_id"
            )
            self.assert_untouched(base, original)
            self.assertEqual(self.snapshot_names(box), ["job-1.md"])

    def test_alias_conflict_is_reported_in_both_directions(self):
        with self.sandbox() as box:
            self.save_ok(box, job=job_with(canonical_job_id="JOB-1"))

            self.save_expecting_rejection(
                box,
                "canonical_job_id_alias_conflict",
                "canonical_job_id",
                job=job_with(canonical_job_id="job-1"),
            )
            self.assertEqual(self.snapshot_names(box), ["JOB-1.md"])


class TimestampTests(SaveJobSnapshotTestCase):
    def test_retrieved_at_must_be_an_rfc3339_instant(self):
        for label, value in INVALID_TIMESTAMPS:
            with self.subTest(retrieved_at=label):
                with self.sandbox() as box:
                    self.save_expecting_rejection(
                        box,
                        "retrieved_at_invalid",
                        "retrieved_at",
                        job=job_with(
                            retrieved_at=value,
                            last_verified_at="2026-12-01T00:00:00Z",
                        ),
                    )

    def test_last_verified_at_must_be_an_rfc3339_instant(self):
        for label, value in INVALID_TIMESTAMPS:
            with self.subTest(last_verified_at=label):
                with self.sandbox() as box:
                    self.save_expecting_rejection(
                        box,
                        "last_verified_at_invalid",
                        "last_verified_at",
                        job=job_with(
                            retrieved_at="2026-01-01T00:00:00Z",
                            last_verified_at=value,
                        ),
                    )

    def test_offset_and_zulu_instants_are_accepted(self):
        for value in VALID_TIMESTAMPS:
            with self.subTest(retrieved_at=value):
                with self.sandbox() as box:
                    report = self.save_ok(
                        box,
                        job=job_with(
                            retrieved_at=value,
                            last_verified_at="2026-12-01T00:00:00Z",
                        ),
                    )
                    self.assertEqual(report["status"], "created")

    def test_last_verified_at_before_retrieved_at_is_rejected(self):
        with self.sandbox() as box:
            self.save_expecting_rejection(
                box,
                "last_verified_at_before_retrieved_at",
                "last_verified_at",
                job=job_with(
                    retrieved_at="2026-09-11T07:00:54Z",
                    last_verified_at="2026-09-10T07:00:54Z",
                ),
            )

    def test_ordering_compares_instants_not_text(self):
        with self.sandbox() as box:
            # 2026-09-11T16:00:54+10:00 is 06:00:54Z, an hour BEFORE retrieval,
            # even though it sorts later as text.
            self.save_expecting_rejection(
                box,
                "last_verified_at_before_retrieved_at",
                "last_verified_at",
                job=job_with(
                    retrieved_at="2026-09-11T07:00:54Z",
                    last_verified_at="2026-09-11T16:00:54+10:00",
                ),
            )

        with self.sandbox() as box:
            # 2026-09-11T18:00:54+10:00 is 08:00:54Z, an hour after retrieval.
            report = self.save_ok(
                box,
                job=job_with(
                    retrieved_at="2026-09-11T07:00:54Z",
                    last_verified_at="2026-09-11T18:00:54+10:00",
                ),
            )
            self.assertEqual(report["status"], "created")

    def test_equal_timestamps_are_accepted(self):
        with self.sandbox() as box:
            report = self.save_ok(
                box,
                job=job_with(
                    retrieved_at="2026-09-11T07:00:54Z",
                    last_verified_at="2026-09-11T07:00:54Z",
                ),
            )

        self.assertEqual(report["status"], "created")


BASE_JOB = job_with(
    retrieved_at="2026-09-10T00:00:00Z",
    last_verified_at="2026-09-10T00:00:00Z",
)

REVERIFIED_JOB = job_with(
    retrieved_at="2026-09-11T07:00:54Z",
    last_verified_at="2026-09-11T07:00:54Z",
)

CHANGED_JOB = job_with(
    retrieved_at="2026-09-11T07:00:54Z",
    last_verified_at="2026-09-11T07:00:54Z",
    job_description="We are hiring TWO backend engineers in Melbourne.\n",
)

BASE_RELATIVE_PATH = "jobs/snapshots/job-1.md"
VERSIONED_RELATIVE_PATH = "jobs/snapshots/job-1--20260911T070054Z.md"


class SnapshotRenderingTests(SaveJobSnapshotTestCase):
    def test_frontmatter_records_exactly_the_agreed_provenance(self):
        with self.sandbox() as box:
            report = self.save_ok(box)
            frontmatter, _body = self.read_snapshot(self.snapshot_file(box, report))

        self.assertIsInstance(frontmatter, dict)
        self.assertEqual(set(frontmatter), FRONTMATTER_KEYS)
        for key in FRONTMATTER_KEYS - {"content_digest"}:
            self.assertEqual(frontmatter[key], VALID_JOB[key], f"frontmatter {key}")
        self.assertEqual(frontmatter["content_digest"], report["content_digest"])

    def test_job_description_is_written_verbatim_byte_for_byte(self):
        with self.sandbox() as box:
            report = self.save_ok(box, job=job_with(job_description=RICH_DESCRIPTION))
            path = self.snapshot_file(box, report)
            _frontmatter, body = self.read_snapshot(path)
            raw = path.read_bytes()

        self.assertEqual(body, RICH_DESCRIPTION)
        self.assertIn(RICH_DESCRIPTION.encode("utf-8"), raw)

    def test_rendering_is_deterministic_across_workspaces(self):
        renderings = []
        for _run in range(2):
            with self.sandbox() as box:
                report = self.save_ok(
                    box, job=job_with(job_description=RICH_DESCRIPTION)
                )
                renderings.append(self.snapshot_file(box, report).read_bytes())

        self.assertEqual(renderings[0], renderings[1])

    def test_snapshot_path_is_workspace_relative_with_forward_slashes(self):
        with self.sandbox() as box:
            report = self.save_ok(box)

            self.assertEqual(report["jd_snapshot_path"], BASE_RELATIVE_PATH)
            self.assertFalse(os.path.isabs(report["jd_snapshot_path"]))
            self.assertNotIn("\\", report["jd_snapshot_path"])
            self.assertTrue((box.workspace / report["jd_snapshot_path"]).is_file())

    def test_tracker_files_are_never_written(self):
        with self.sandbox() as box:
            self.pin_mtime(box.applications_csv)
            self.pin_mtime(box.events_jsonl)
            applications = box.applications_csv.read_bytes()
            events = box.events_jsonl.read_bytes()

            self.save_ok(box)

            self.assert_untouched(box.applications_csv, applications)
            self.assert_untouched(box.events_jsonl, events)


class ContentDigestTests(SaveJobSnapshotTestCase):
    def digest_for(self, job):
        with self.sandbox() as box:
            return self.save_ok(box, job=job)["content_digest"]

    def test_digest_is_a_sha256_hex_string(self):
        self.assertRegex(self.digest_for(VALID_JOB), DIGEST_PATTERN)

    def test_digest_ignores_retrieval_and_verification_timestamps(self):
        baseline = self.digest_for(BASE_JOB)
        variants = {
            "retrieved_at": job_with(
                retrieved_at="2026-09-11T07:00:54Z",
                last_verified_at="2026-09-11T07:00:54Z",
            ),
            "last_verified_at": job_with(
                retrieved_at="2026-09-10T00:00:00Z",
                last_verified_at="2026-09-30T00:00:00Z",
            ),
        }

        for field in PROVENANCE_ONLY_FIELDS:
            with self.subTest(non_material_field=field):
                self.assertEqual(
                    self.digest_for(variants[field]),
                    baseline,
                    "re-fetching an unchanged ad must not read as a material change",
                )

    def test_digest_changes_with_every_material_field(self):
        baseline = self.digest_for(VALID_JOB)
        replacements = {
            "job_description": "A different advertisement entirely.\n",
            "source": "another_board",
            "canonical_url": "https://example.com/jobs/9999",
            "job_title": "Senior Backend Engineer",
            "company": "Great Barrier Systems Pty Ltd",
            "location": "Sydney NSW 2000, Australia",
        }

        for field in MATERIAL_FIELDS:
            with self.subTest(material_field=field):
                self.assertNotEqual(
                    self.digest_for(job_with(**{field: replacements[field]})),
                    baseline,
                )


class IdempotencyAndVersioningTests(SaveJobSnapshotTestCase):
    def create_base(self, box, job=BASE_JOB):
        report = self.save_ok(box, job=job)
        self.assertEqual(report["status"], "created")
        self.assertEqual(report["jd_snapshot_path"], BASE_RELATIVE_PATH)
        path = self.snapshot_file(box, report)
        self.pin_mtime(path)
        return report, path, path.read_bytes()

    def test_first_save_creates_the_base_snapshot(self):
        with self.sandbox() as box:
            report, path, _bytes = self.create_base(box)

            self.assertEqual(report["status"], "created")
            self.assertTrue(path.is_file())
            self.assertEqual(self.snapshot_names(box), ["job-1.md"])

    def test_identical_input_writes_nothing_and_reports_unchanged(self):
        with self.sandbox() as box:
            first, path, original = self.create_base(box)

            report = self.save_ok(box, job=BASE_JOB)

            self.assertEqual(report["status"], "unchanged")
            self.assertEqual(report["jd_snapshot_path"], BASE_RELATIVE_PATH)
            self.assertEqual(report["content_digest"], first["content_digest"])
            self.assert_untouched(path, original)
            self.assertEqual(self.snapshot_names(box), ["job-1.md"])

    def test_reverification_rewrites_an_unreferenced_base(self):
        with self.sandbox() as box:
            first, path, original = self.create_base(box)

            report = self.save_ok(box, job=REVERIFIED_JOB)

            self.assertEqual(report["status"], "updated")
            self.assertEqual(report["jd_snapshot_path"], BASE_RELATIVE_PATH)
            self.assertEqual(report["content_digest"], first["content_digest"])
            self.assertNotEqual(path.read_bytes(), original)
            frontmatter, _body = self.read_snapshot(path)
            self.assertEqual(frontmatter["retrieved_at"], "2026-09-11T07:00:54Z")
            self.assertEqual(frontmatter["last_verified_at"], "2026-09-11T07:00:54Z")
            self.assertEqual(self.snapshot_names(box), ["job-1.md"])

    def test_reverification_leaves_a_referenced_base_immutable(self):
        for label, reference in (
            ("applications_csv", self.reference_in_applications_csv),
            ("events_jsonl", self.reference_in_events_jsonl),
        ):
            with self.subTest(referenced_via=label):
                with self.sandbox() as box:
                    first, path, original = self.create_base(box)
                    reference(box, BASE_RELATIVE_PATH)

                    report = self.save_ok(box, job=REVERIFIED_JOB)

                    self.assertEqual(report["status"], "unchanged")
                    self.assertEqual(report["jd_snapshot_path"], BASE_RELATIVE_PATH)
                    self.assertEqual(report["content_digest"], first["content_digest"])
                    self.assert_untouched(path, original)
                    self.assertEqual(
                        self.snapshot_names(box),
                        ["job-1.md"],
                        "a mere re-verification must not mint a timestamped file",
                    )

    def test_material_change_rewrites_an_unreferenced_base(self):
        with self.sandbox() as box:
            first, path, original = self.create_base(box)

            report = self.save_ok(box, job=CHANGED_JOB)

            self.assertEqual(report["status"], "updated")
            self.assertEqual(report["jd_snapshot_path"], BASE_RELATIVE_PATH)
            self.assertNotEqual(report["content_digest"], first["content_digest"])
            self.assertNotEqual(path.read_bytes(), original)
            self.assertEqual(self.snapshot_names(box), ["job-1.md"])

    def test_material_change_versions_a_referenced_base(self):
        for label, reference in (
            ("applications_csv", self.reference_in_applications_csv),
            ("events_jsonl", self.reference_in_events_jsonl),
        ):
            with self.subTest(referenced_via=label):
                with self.sandbox() as box:
                    first, path, original = self.create_base(box)
                    reference(box, BASE_RELATIVE_PATH)

                    report = self.save_ok(box, job=CHANGED_JOB)

                    self.assertEqual(report["status"], "versioned")
                    self.assertEqual(
                        report["jd_snapshot_path"], VERSIONED_RELATIVE_PATH
                    )
                    self.assertNotEqual(
                        report["content_digest"], first["content_digest"]
                    )
                    self.assert_untouched(path, original)
                    self.assertEqual(
                        self.snapshot_names(box),
                        ["job-1--20260911T070054Z.md", "job-1.md"],
                    )
                    versioned = self.snapshot_file(box, report)
                    _frontmatter, body = self.read_snapshot(versioned)
                    self.assertEqual(body, CHANGED_JOB["job_description"])

    def test_versioned_filename_normalises_an_offset_to_utc(self):
        with self.sandbox() as box:
            _first, path, original = self.create_base(box)
            self.reference_in_applications_csv(box, BASE_RELATIVE_PATH)

            report = self.save_ok(
                box,
                job=job_with(
                    retrieved_at="2026-09-11T17:00:54+10:00",
                    last_verified_at="2026-09-11T17:00:54+10:00",
                    job_description="Changed advertisement text.\n",
                ),
            )

            self.assertEqual(report["status"], "versioned")
            self.assertEqual(report["jd_snapshot_path"], VERSIONED_RELATIVE_PATH)
            self.assert_untouched(path, original)

    def test_unrelated_tracker_rows_do_not_reference_this_snapshot(self):
        with self.sandbox() as box:
            _first, path, original = self.create_base(box)
            self.reference_in_applications_csv(
                box, "jobs/snapshots/other-job.md", job_id="other-job"
            )
            self.reference_in_events_jsonl(
                box, "jobs/snapshots/other-job.md", job_id="other-job"
            )

            report = self.save_ok(box, job=CHANGED_JOB)

            self.assertEqual(report["status"], "updated")
            self.assertEqual(report["jd_snapshot_path"], BASE_RELATIVE_PATH)
            self.assertNotEqual(path.read_bytes(), original)
            self.assertEqual(self.snapshot_names(box), ["job-1.md"])

    def test_version_collision_with_identical_content_is_unchanged(self):
        with self.sandbox() as box:
            _first, base, base_bytes = self.create_base(box)
            self.reference_in_applications_csv(box, BASE_RELATIVE_PATH)
            first_version = self.save_ok(box, job=CHANGED_JOB)
            self.assertEqual(first_version["status"], "versioned")
            versioned = self.snapshot_file(box, first_version)
            versioned_bytes = versioned.read_bytes()
            self.pin_mtime(versioned)

            report = self.save_ok(box, job=CHANGED_JOB)

            self.assertEqual(report["status"], "unchanged")
            self.assertEqual(report["jd_snapshot_path"], VERSIONED_RELATIVE_PATH)
            self.assert_untouched(versioned, versioned_bytes)
            self.assert_untouched(base, base_bytes)
            self.assertEqual(
                self.snapshot_names(box), ["job-1--20260911T070054Z.md", "job-1.md"]
            )

    def test_version_collision_with_different_content_is_rejected(self):
        with self.sandbox() as box:
            _first, base, base_bytes = self.create_base(box)
            self.reference_in_applications_csv(box, BASE_RELATIVE_PATH)
            occupied = box.workspace / VERSIONED_RELATIVE_PATH
            occupied.write_bytes(b"an unrelated earlier version\n")
            self.pin_mtime(occupied)

            _result, report = self.save_expecting_rejection(
                box, "snapshot_version_collision", None, job=CHANGED_JOB
            )

            self.assertEqual(
                self.codes(report["errors"]), ["snapshot_version_collision"]
            )
            self.assert_untouched(occupied, b"an unrelated earlier version\n")
            self.assert_untouched(base, base_bytes)
            self.assertEqual(
                self.snapshot_names(box), ["job-1--20260911T070054Z.md", "job-1.md"]
            )


class TrackerFailClosedTests(SaveJobSnapshotTestCase):
    def create_referenced_looking_base(self, box):
        report = self.save_ok(box, job=BASE_JOB)
        self.assertEqual(report["status"], "created")
        path = self.snapshot_file(box, report)
        self.pin_mtime(path)
        return path, path.read_bytes()

    def assert_treated_as_referenced(self, box, base, base_bytes):
        report = self.save_ok(box, job=CHANGED_JOB)

        self.assertEqual(
            report["status"],
            "versioned",
            "an unreadable tracker must fail closed and treat the base as immutable",
        )
        self.assertEqual(report["jd_snapshot_path"], VERSIONED_RELATIVE_PATH)
        self.assert_untouched(base, base_bytes)

    def test_undecodable_applications_csv_fails_closed(self):
        with self.sandbox() as box:
            base, base_bytes = self.create_referenced_looking_base(box)
            box.applications_csv.write_bytes(b"\xff\xfe not,valid,utf-8\n")

            self.assert_treated_as_referenced(box, base, base_bytes)

    def test_unparsable_events_jsonl_fails_closed(self):
        with self.sandbox() as box:
            base, base_bytes = self.create_referenced_looking_base(box)
            box.events_jsonl.write_text("{not json at all\n", encoding="utf-8")

            self.assert_treated_as_referenced(box, base, base_bytes)

    @unittest.skipIf(RUNNING_AS_ROOT, "root bypasses file permissions")
    def test_unreadable_applications_csv_fails_closed(self):
        with self.sandbox() as box:
            base, base_bytes = self.create_referenced_looking_base(box)
            os.chmod(box.applications_csv, 0o000)
            try:
                self.assert_treated_as_referenced(box, base, base_bytes)
            finally:
                os.chmod(box.applications_csv, 0o600)

    @unittest.skipIf(RUNNING_AS_ROOT, "root bypasses file permissions")
    def test_unreadable_events_jsonl_fails_closed(self):
        with self.sandbox() as box:
            base, base_bytes = self.create_referenced_looking_base(box)
            os.chmod(box.events_jsonl, 0o000)
            try:
                self.assert_treated_as_referenced(box, base, base_bytes)
            finally:
                os.chmod(box.events_jsonl, 0o600)


class WorkspaceContainmentTests(SaveJobSnapshotTestCase):
    def replace_with_directory_symlink(self, path, target):
        shutil.rmtree(path)
        path.symlink_to(target, target_is_directory=True)
        self.assertTrue(path.is_symlink())

    def assert_symlink_intact(self, link, target):
        self.assertTrue(link.is_symlink(), "the user's symlink was removed or replaced")
        self.assertEqual(
            os.readlink(link), str(target), "the user's symlink was re-pointed"
        )

    def test_workspace_must_exist(self):
        with self.sandbox() as box:
            self.save_expecting_rejection(
                box,
                "workspace_unsafe",
                "workspace",
                workspace=box.root / "no-such-workspace",
            )

    def test_workspace_must_be_a_directory(self):
        with self.sandbox() as box:
            plain_file = box.root / "not-a-directory"
            plain_file.write_text("x\n", encoding="utf-8")

            self.save_expecting_rejection(
                box, "workspace_unsafe", "workspace", workspace=plain_file
            )

    def test_workspace_must_not_be_a_symlink(self):
        with self.sandbox() as box:
            link = box.root / "workspace-link"
            link.symlink_to(box.workspace, target_is_directory=True)

            self.save_expecting_rejection(
                box, "workspace_unsafe", "workspace", workspace=link
            )
            self.assert_symlink_intact(link, box.workspace)
            self.assertEqual(self.snapshot_names(box), [])

    def test_workspace_must_not_be_the_skill_repository(self):
        with self.sandbox() as box:
            self.save_expecting_rejection(
                box, "workspace_unsafe", "workspace", workspace=ROOT
            )

        self.assertFalse((ROOT / "jobs").exists(), "the repository must stay clean")
        self.assertFalse((ROOT / "tracker").exists(), "the repository must stay clean")

    def test_workspace_must_not_be_inside_the_skill_repository(self):
        inside = ROOT / "tests"

        with self.sandbox() as box:
            self.save_expecting_rejection(
                box, "workspace_unsafe", "workspace", workspace=inside
            )

        self.assertFalse((inside / "jobs").exists(), "the repository must stay clean")
        self.assertFalse((inside / "tracker").exists(), "the repository must stay clean")

    def test_jobs_directory_symlink_pointing_outside_is_refused(self):
        with self.sandbox() as box:
            target = box.outside / "stolen-jobs"
            target.mkdir()
            (target / "snapshots").mkdir()
            link = box.workspace / "jobs"
            self.replace_with_directory_symlink(link, target)

            _result, report = self.save_expecting_rejection(
                box, "snapshot_path_unsafe", None
            )

            self.assertEqual(self.codes(report["errors"]), ["snapshot_path_unsafe"])
            self.assert_symlink_intact(link, target)
            self.assertEqual(list((target / "snapshots").iterdir()), [])

    def test_jobs_directory_symlink_pointing_inside_is_refused(self):
        with self.sandbox() as box:
            target = box.workspace / "reports"
            link = box.workspace / "jobs"
            self.replace_with_directory_symlink(link, target)

            self.save_expecting_rejection(box, "snapshot_path_unsafe", None)

            self.assert_symlink_intact(link, target)
            self.assertEqual(list(target.iterdir()), [])

    def test_snapshots_directory_symlink_pointing_outside_is_refused(self):
        with self.sandbox() as box:
            target = box.outside / "stolen-snapshots"
            target.mkdir()
            link = box.snapshots
            self.replace_with_directory_symlink(link, target)

            self.save_expecting_rejection(box, "snapshot_path_unsafe", None)

            self.assert_symlink_intact(link, target)
            self.assertEqual(list(target.iterdir()), [])

    def test_snapshots_directory_symlink_pointing_inside_is_refused(self):
        with self.sandbox() as box:
            target = box.workspace / "reports"
            link = box.snapshots
            self.replace_with_directory_symlink(link, target)

            self.save_expecting_rejection(box, "snapshot_path_unsafe", None)

            self.assert_symlink_intact(link, target)
            self.assertEqual(list(target.iterdir()), [])

    def test_target_snapshot_file_symlink_is_refused(self):
        with self.sandbox() as box:
            target = box.outside / "leak.md"
            target.write_text("private notes\n", encoding="utf-8")
            link = box.snapshots / "job-1.md"
            link.symlink_to(target)

            self.save_expecting_rejection(box, "snapshot_path_unsafe", None)

            self.assert_symlink_intact(link, target)
            self.assertEqual(target.read_text(encoding="utf-8"), "private notes\n")

    def test_a_sibling_sharing_the_workspace_name_prefix_is_still_outside(self):
        with self.sandbox() as box:
            # ".../private-workspace-evil/snapshots" starts with
            # ".../private-workspace", so a string-prefix containment check
            # would call it contained. The symlink walk refuses this planted
            # link before the resolution comparison is reached, so what this
            # case pins is the symlink rule, not the resolution rule. No input
            # can reach the resolution comparison without a symlink component,
            # because canonical_job_id may contain neither a path separator
            # nor "..", so there is no symlink-free escape left to test.
            sibling = box.root / (box.workspace.name + "-evil")
            target = sibling / "snapshots"
            target.mkdir(parents=True)
            self.assertTrue(str(target).startswith(str(box.workspace)))
            link = box.snapshots
            self.replace_with_directory_symlink(link, target)

            self.save_expecting_rejection(box, "snapshot_path_unsafe", None)

            self.assert_symlink_intact(link, target)
            self.assertEqual(list(target.iterdir()), [])

    def test_a_symlink_at_the_version_filename_is_refused(self):
        with self.sandbox() as box:
            first = self.save_ok(box, job=BASE_JOB)
            base = self.snapshot_file(box, first)
            base_bytes = base.read_bytes()
            self.pin_mtime(base)
            self.reference_in_applications_csv(box, BASE_RELATIVE_PATH)
            target = box.outside / "leak-version.md"
            target.write_text("private notes\n", encoding="utf-8")
            link = box.workspace / VERSIONED_RELATIVE_PATH
            link.symlink_to(target)

            self.save_expecting_rejection(
                box, "snapshot_path_unsafe", None, job=CHANGED_JOB
            )

            self.assert_symlink_intact(link, target)
            self.assertEqual(target.read_text(encoding="utf-8"), "private notes\n")
            self.assert_untouched(base, base_bytes)

    def test_a_hard_linked_snapshot_target_is_refused(self):
        with self.sandbox() as box:
            target = box.outside / "hard-linked-notes.md"
            target.write_text("private notes\n", encoding="utf-8")
            os.link(target, box.snapshots / "job-1.md")
            self.assertEqual(target.stat().st_nlink, 2)

            self.save_expecting_rejection(box, "snapshot_path_unsafe", None)

            self.assertEqual(target.read_text(encoding="utf-8"), "private notes\n")
            self.assertEqual(
                target.stat().st_nlink,
                2,
                "writing through a hard link would edit the user's other file",
            )


class JobRecordRobustnessTests(SaveJobSnapshotTestCase):
    def test_missing_job_file_is_reported(self):
        with self.sandbox() as box:
            self.save_expecting_rejection(
                box,
                "job_record_unreadable",
                "job",
                job_path=box.inbox / "no-such-job.json",
            )

    def test_directory_as_job_file_is_reported(self):
        with self.sandbox() as box:
            directory = box.inbox / "job-record-directory"
            directory.mkdir()

            self.save_expecting_rejection(
                box,
                "job_record_unreadable",
                "job",
                job_path=directory,
                timeout=NON_REGULAR_TIMEOUT_SECONDS,
            )

    def test_fifo_as_job_file_is_reported_without_hanging(self):
        with self.sandbox() as box:
            fifo = box.inbox / "job-record-fifo"
            os.mkfifo(fifo)
            try:
                result = self.run_saver(
                    "--workspace",
                    box.workspace,
                    "--job",
                    fifo,
                    timeout=NON_REGULAR_TIMEOUT_SECONDS,
                )
            finally:
                fifo.unlink()

            report = self.parse_report(result, secrets=box.secrets())
            self.assert_rejected(result, report, "job_record_unreadable", "job")
            self.assertEqual(self.snapshot_names(box), [])

    @unittest.skipIf(RUNNING_AS_ROOT, "root bypasses file permissions")
    def test_unreadable_job_file_is_reported(self):
        with self.sandbox() as box:
            job_path = self.write_job(box)
            os.chmod(job_path, 0o000)
            try:
                self.save_expecting_rejection(
                    box, "job_record_unreadable", "job", job_path=job_path
                )
            finally:
                os.chmod(job_path, 0o600)

    def test_malformed_json_is_reported(self):
        for label, raw in (
            ("truncated", '{"canonical_job_id": "job-1"'),
            ("trailing_comma", '{"canonical_job_id": "job-1",}'),
            ("not_json", "canonical_job_id: job-1\n"),
            ("empty", ""),
        ):
            with self.subTest(job_record=label):
                with self.sandbox() as box:
                    self.save_expecting_rejection(
                        box, "job_record_malformed", "job", raw=raw
                    )

    def test_json_that_is_not_a_mapping_is_reported(self):
        for label, raw in (
            ("list", '[{"canonical_job_id": "job-1"}]'),
            ("string", '"job-1"'),
            ("number", "7"),
            ("null", "null"),
            ("boolean", "true"),
        ):
            with self.subTest(job_record=label):
                with self.sandbox() as box:
                    self.save_expecting_rejection(
                        box, "job_record_not_a_mapping", "job", raw=raw
                    )

    def test_both_arguments_are_required(self):
        with self.sandbox() as box:
            job_path = self.write_job(box)
            invocations = (
                ("no_arguments", ()),
                ("workspace_only", ("--workspace", box.workspace)),
                ("job_only", ("--job", job_path)),
            )

            for label, arguments in invocations:
                with self.subTest(invocation=label):
                    result = self.run_saver(*arguments)

                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("required", result.stderr.lower())
                    self.assertNotIn("traceback", result.stderr.lower())

    @unittest.skipIf(RUNNING_AS_ROOT, "root bypasses file permissions")
    def test_a_failed_write_leaves_no_partial_snapshot_behind(self):
        with self.sandbox() as box:
            os.chmod(box.snapshots, 0o500)
            try:
                result = self.run_saver("--workspace", box.workspace, "--job", self.write_job(box))
                report = self.parse_report(result, secrets=box.secrets())

                self.assertEqual(result.returncode, 1)
                self.assertEqual(report["status"], "rejected")
                self.assertIsNone(report["jd_snapshot_path"])
                self.assertEqual(
                    self.codes(report["errors"]), ["snapshot_write_failed"]
                )
                self.assertEqual(
                    self.fields_for(report["errors"], "snapshot_write_failed"),
                    ["jd_snapshot_path"],
                )
                self.assertEqual(
                    sorted(path.name for path in box.snapshots.iterdir()),
                    [],
                    "no snapshot, temporary file or stray dotfile may be left behind",
                )
            finally:
                os.chmod(box.snapshots, 0o700)


# ---------------------------------------------------------------------------
# Remediation round.
#
# Everything below pins the findings from the two review gates: the reserved
# version namespace, whole-stem alias matching, reference detection that
# survives ordinary path spellings, out-of-range instants, a bounded tracker
# read, an http(s)-only canonical_url, the validate-then-write race, the
# rejection report's identity fields, and the snapshot file mode.
# ---------------------------------------------------------------------------


RESERVED_JOB_IDS = (
    ("version_shaped", "job-1--20260911T070054Z"),
    ("trailing_word", "job--x"),
    ("minimal", "a--b"),
    ("bare_separator", "--"),
)

SINGLE_DASH_JOB_IDS = ("job-1", "a-b-c")


class VersionNamespaceReservationTests(SaveJobSnapshotTestCase):
    """``--`` separates an identifier from its version stamp, so an identifier
    may never contain it.  ``job-1--20260911T070054Z`` is otherwise a legal id
    whose base path is byte-identical to the version path minted for
    ``job-1``, which lets a scraped identifier overwrite an archived,
    referenced snapshot."""

    def test_identifiers_containing_the_version_separator_are_reserved(self):
        for label, value in RESERVED_JOB_IDS:
            with self.subTest(reserved=label):
                with self.sandbox() as box:
                    self.save_expecting_rejection(
                        box,
                        "canonical_job_id_reserved",
                        "canonical_job_id",
                        job=job_with(canonical_job_id=value),
                    )
                    self.assertEqual(self.snapshot_names(box), [])

        for value in SINGLE_DASH_JOB_IDS:
            with self.subTest(single_dash=value):
                with self.sandbox() as box:
                    report = self.save_ok(box, job=job_with(canonical_job_id=value))

                    self.assertEqual(
                        report["jd_snapshot_path"], f"jobs/snapshots/{value}.md"
                    )
                    self.assertEqual(self.snapshot_names(box), [f"{value}.md"])

    def test_a_reserved_identifier_cannot_overwrite_an_archived_version(self):
        with self.sandbox() as box:
            self.save_ok(box, job=BASE_JOB)
            self.reference_in_applications_csv(box, BASE_RELATIVE_PATH)
            versioned = self.save_ok(box, job=CHANGED_JOB)
            self.assertEqual(versioned["status"], "versioned")
            self.assertEqual(versioned["jd_snapshot_path"], VERSIONED_RELATIVE_PATH)
            archived = box.workspace / VERSIONED_RELATIVE_PATH
            archived_bytes = archived.read_bytes()
            self.pin_mtime(archived)

            self.save_expecting_rejection(
                box,
                "canonical_job_id_reserved",
                "canonical_job_id",
                job=job_with(
                    canonical_job_id="job-1--20260911T070054Z",
                    job_description="An advertisement supplied by the job board.\n",
                ),
            )

            self.assert_untouched(archived, archived_bytes)
            self.assertEqual(
                self.snapshot_names(box),
                ["job-1--20260911T070054Z.md", "job-1.md"],
            )


ALIAS_CASES = (
    # label, stored snapshot file names, identifier to save, conflict expected
    ("base_and_its_version", ("job-1.md", "job-1--20260911T070054Z.md"), "JOB-1", True),
    ("version_only", ("job-1--20260911T070054Z.md",), "JOB-1", True),
    ("unrelated_identifier", ("job-1--20260911T070054Z.md",), "other-job", False),
    ("same_stem", ("foo.md",), "FOO", True),
    ("longer_stored_stem", ("foobar.md",), "FOO", False),
    ("stored_name_is_not_a_version", ("job-1--notes.md",), "JOB-1", False),
)


class AliasConflictTests(SaveJobSnapshotTestCase):
    """The alias check must compare the whole case-folded stem after stripping
    a trailing ``--<YYYYMMDDTHHMMSSZ>`` version stamp.  Splitting on the first
    ``--`` and comparing the prefix both under- and over-matches."""

    def plant(self, box, name):
        (box.snapshots / name).write_text("planted\n", encoding="utf-8")

    def test_alias_conflict_matches_the_whole_case_folded_stem(self):
        for label, stored, identifier, conflicts in ALIAS_CASES:
            with self.subTest(alias=label):
                with self.sandbox() as box:
                    for name in stored:
                        self.plant(box, name)

                    if conflicts:
                        self.save_expecting_rejection(
                            box,
                            "canonical_job_id_alias_conflict",
                            "canonical_job_id",
                            job=job_with(canonical_job_id=identifier),
                        )
                        self.assertEqual(self.snapshot_names(box), sorted(stored))
                    else:
                        report = self.save_ok(
                            box, job=job_with(canonical_job_id=identifier)
                        )

                        self.assertEqual(
                            report["jd_snapshot_path"],
                            f"jobs/snapshots/{identifier}.md",
                        )
                        self.assertEqual(
                            self.snapshot_names(box),
                            sorted([*stored, f"{identifier}.md"]),
                        )


UNREFERENCED_SPELLINGS = (
    ("another_snapshot", "jobs/snapshots/other.md"),
    ("longer_identifier", "jobs/snapshots/job-10.md"),
    ("the_path_as_a_substring", "jobs/snapshots/job-1.md.bak"),
)


class ReferenceDetectionTests(SaveJobSnapshotTestCase):
    """A snapshot that the tracker points at is immutable.  Missing an
    ordinary spelling of the path reads as 'unreferenced' and rewrites a
    snapshot other records already cite."""

    def create_base(self, box):
        report = self.save_ok(box, job=BASE_JOB)
        self.assertEqual(report["status"], "created")
        path = self.snapshot_file(box, report)
        self.pin_mtime(path)
        return path, path.read_bytes()

    def reference_nested_in_events_jsonl(self, box, snapshot_path):
        event = {
            "event": "jd_snapshot_recorded",
            "payload": {
                "canonical_job_id": "job-1",
                "jd_snapshot_path": snapshot_path,
            },
        }
        with box.events_jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")

    def assert_referenced(self, box, base, base_bytes):
        report = self.save_ok(box, job=CHANGED_JOB)

        self.assertEqual(
            report["status"],
            "versioned",
            "a snapshot the tracker cites must be versioned, never rewritten",
        )
        self.assertEqual(report["jd_snapshot_path"], VERSIONED_RELATIVE_PATH)
        self.assert_untouched(base, base_bytes)

    def assert_unreferenced(self, box, base, base_bytes):
        report = self.save_ok(box, job=CHANGED_JOB)

        self.assertEqual(
            report["status"],
            "updated",
            "an unrelated tracker row must not freeze this snapshot",
        )
        self.assertEqual(report["jd_snapshot_path"], BASE_RELATIVE_PATH)
        self.assertNotEqual(base.read_bytes(), base_bytes)
        self.assertEqual(self.snapshot_names(box), ["job-1.md"])

    def test_reference_detection_normalises_ordinary_path_spellings(self):
        with self.subTest(reference="applications_csv_leading_dot_slash"):
            with self.sandbox() as box:
                base, base_bytes = self.create_base(box)
                self.reference_in_applications_csv(box, "./" + BASE_RELATIVE_PATH)

                self.assert_referenced(box, base, base_bytes)

        with self.subTest(reference="applications_csv_absolute_path"):
            with self.sandbox() as box:
                base, base_bytes = self.create_base(box)
                absolute = box.workspace.resolve() / BASE_RELATIVE_PATH
                self.reference_in_applications_csv(box, str(absolute))

                self.assert_referenced(box, base, base_bytes)

        with self.subTest(reference="events_jsonl_nested_field"):
            with self.sandbox() as box:
                base, base_bytes = self.create_base(box)
                self.reference_nested_in_events_jsonl(box, BASE_RELATIVE_PATH)

                self.assert_referenced(box, base, base_bytes)

        for label, spelling in UNREFERENCED_SPELLINGS:
            with self.subTest(unreferenced=label):
                with self.sandbox() as box:
                    base, base_bytes = self.create_base(box)
                    self.reference_in_applications_csv(
                        box, spelling, job_id="other-job"
                    )
                    self.reference_nested_in_events_jsonl(box, spelling)

                    self.assert_unreferenced(box, base, base_bytes)


OVERSIZE_TRACKER_BYTES = 128 * 1024 * 1024
TRACKER_ROW_PADDING = "u" * 4000


class TrackerSizeTests(SaveJobSnapshotTestCase):
    """Reading a tracker whole is unbounded work on attacker-influenced data.
    An OOM kill produces no stdout at all, which breaks the 'always exactly
    one JSON object' contract in the one way a caller cannot recover from, so
    an oversize tracker must be refused by a stat check and fail closed."""

    def create_base(self, box):
        report = self.save_ok(box, job=BASE_JOB)
        self.assertEqual(report["status"], "created")
        path = self.snapshot_file(box, report)
        self.pin_mtime(path)
        return path, path.read_bytes()

    def write_oversize_applications_csv(self, box):
        # Written as real rows rather than a sparse hole: a sparse region is
        # NUL bytes, which the csv reader rejects outright and which would
        # therefore fail closed today for the wrong reason.
        row = [
            "unrelated-job",
            "jobs/snapshots/unrelated.md",
            "example_board",
            "https://example.com/jobs/unrelated",
            "Backend Engineer",
            TRACKER_ROW_PADDING,
            "Melbourne VIC 3000, Australia",
            "", "", "", "", "", "", "", "",
        ]
        line_length = len(",".join(row)) + 1
        with box.applications_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(APPLICATION_HEADER)
            for _ in range(OVERSIZE_TRACKER_BYTES // line_length + 1):
                writer.writerow(row)
        self.assertGreater(
            box.applications_csv.stat().st_size, OVERSIZE_TRACKER_BYTES
        )

    def test_an_oversize_applications_csv_fails_closed(self):
        with self.sandbox() as box:
            base, base_bytes = self.create_base(box)
            self.write_oversize_applications_csv(box)

            report = self.save_ok(
                box, job=CHANGED_JOB, timeout=SUBPROCESS_TIMEOUT_SECONDS
            )

            self.assertEqual(
                report["status"],
                "versioned",
                "a tracker too large to read must be treated as a reference",
            )
            self.assertEqual(report["jd_snapshot_path"], VERSIONED_RELATIVE_PATH)
            self.assert_untouched(base, base_bytes)


INVALID_CANONICAL_URLS = (
    ("file_scheme", "file:///etc/passwd"),
    ("javascript_scheme", "javascript:alert(1)"),
    ("data_scheme", "data:text/html;base64,PHNjcmlwdD4xPC9zY3JpcHQ+"),
    ("no_scheme", "example.com"),
    ("scheme_with_no_host", "http://"),
    ("empty_host_with_path", "https:///jobs/1042"),
)

VALID_CANONICAL_URLS = (
    "http://example.com",
    "https://example.com",
    "https://example.com/jobs/1042",
    "https://example.com:8443/jobs/1042",
    "https://example.com/jobs/1042?ref=board&page=2#requirements",
    "https://jobs.example.com.au/%E4%B8%AD%E6%96%87/1042",
)

URL_REJECTION_CODES = {"canonical_url_invalid", "required_field_invalid"}


class CanonicalUrlTests(SaveJobSnapshotTestCase):
    """canonical_url is copied verbatim into frontmatter, which Task 6 copies
    into the tracker and a dashboard renders as a link, so only http and https
    with a real host may be stored."""

    def test_canonical_url_must_be_http_or_https_with_a_host(self):
        for label, value in INVALID_CANONICAL_URLS:
            with self.subTest(canonical_url=label):
                with self.sandbox() as box:
                    self.save_expecting_rejection(
                        box,
                        "canonical_url_invalid",
                        "canonical_url",
                        job=job_with(canonical_url=value),
                    )
                    self.assertEqual(self.snapshot_names(box), [])

        for value in VALID_CANONICAL_URLS:
            with self.subTest(canonical_url=value):
                with self.sandbox() as box:
                    report = self.save_ok(box, job=job_with(canonical_url=value))

                    self.assertEqual(report["status"], "created")
                    frontmatter, _body = self.read_snapshot(
                        self.snapshot_file(box, report)
                    )
                    self.assertEqual(frontmatter["canonical_url"], value)

    def test_a_whitespace_only_canonical_url_is_rejected(self):
        with self.sandbox() as box:
            job_path = self.write_job(box, job=job_with(canonical_url="   "))
            before = snapshot_tree(box.root)

            result, report = self.save(box, job_path=job_path)

            self.assertEqual(snapshot_tree(box.root), before)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(report["status"], "rejected")
            codes = set(self.codes(report["errors"]))
            self.assertTrue(
                codes & URL_REJECTION_CODES,
                f"a blank canonical_url must be refused; errors were {report['errors']}",
            )


WRAPPER_SOURCE = '''\
import os
import pathlib
import shutil
import sys

sys.path.insert(0, os.environ["SAVER_SCRIPTS_DIRECTORY"])

import save_job_snapshot as saver


def interpose(name, hook):
    original = getattr(saver, name)

    def replacement(*arguments, **keywords):
        outcome = original(*arguments, **keywords)
        hook()
        return outcome

    setattr(saver, name, replacement)


{hook}

saver.main()
'''

SWAP_SNAPSHOTS_DIRECTORY_HOOK = '''\
def swap_the_snapshots_directory():
    snapshots = pathlib.Path(os.environ["SNAPSHOTS_DIRECTORY"])
    target = pathlib.Path(os.environ["SWAP_TARGET"])
    shutil.rmtree(snapshots)
    snapshots.symlink_to(target, target_is_directory=True)


interpose("has_alias_conflict", swap_the_snapshots_directory)
'''

PLANT_VERSION_FILE_HOOK = '''\
def plant_the_version_file():
    pathlib.Path(os.environ["VERSION_PATH"]).write_bytes(
        b"a concurrently written version\\n"
    )


interpose("plan_snapshot", plant_the_version_file)
'''

RAISE_AN_UNEXPECTED_FAULT_HOOK = '''\
def raise_an_unexpected_fault():
    raise RuntimeError("an unexpected internal fault")


interpose("render_snapshot", raise_an_unexpected_fault)
'''

PLANTED_VERSION_BYTES = b"a concurrently written version\n"


class InterposedRunTests(SaveJobSnapshotTestCase):
    """A validate-then-mutate race cannot be provoked from inside the process
    under test, so these run the real script under a wrapper that interposes
    on one module-level function and mutates the workspace between the check
    and the write.  The wrapper is written into the sandbox, never the repo."""

    def run_interposed(
        self,
        box,
        hook,
        job=MISSING,
        environment=None,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    ):
        wrapper = box.root / "interposed-saver.py"
        wrapper.write_text(WRAPPER_SOURCE.format(hook=hook), encoding="utf-8")
        job_path = self.write_job(box, job=job)
        variables = dict(os.environ)
        variables["SAVER_SCRIPTS_DIRECTORY"] = str(SCRIPT.parent)
        variables["PYTHONDONTWRITEBYTECODE"] = "1"
        variables.update(environment or {})
        try:
            result = subprocess.run(
                [
                    "python3",
                    str(wrapper),
                    "--workspace",
                    str(box.workspace),
                    "--job",
                    str(job_path),
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

    def test_a_snapshots_directory_swapped_in_after_validation_is_never_written(self):
        with self.sandbox() as box:
            target = box.outside / "stolen-snapshots"
            target.mkdir()
            outside_before = snapshot_tree(box.outside)

            result, report = self.run_interposed(
                box,
                SWAP_SNAPSHOTS_DIRECTORY_HOOK,
                environment={
                    "SNAPSHOTS_DIRECTORY": str(box.snapshots),
                    "SWAP_TARGET": str(target),
                },
            )

            self.assertEqual(
                snapshot_tree(box.outside),
                outside_before,
                "validation and the write must refer to the same directory inode; "
                "a directory swapped in afterwards must never be written through",
            )
            self.assertEqual(list(target.iterdir()), [])
            self.assertNotEqual(
                report["errors"],
                [],
                "a run whose snapshots directory was swapped must not report success",
            )
            self.assertEqual(report["status"], "rejected")
            self.assertIsNone(report["jd_snapshot_path"])
            self.assertEqual(result.returncode, 1)

    def test_a_version_file_created_after_planning_is_not_overwritten(self):
        with self.sandbox() as box:
            self.save_ok(box, job=BASE_JOB)
            self.reference_in_applications_csv(box, BASE_RELATIVE_PATH)
            version = box.workspace / VERSIONED_RELATIVE_PATH

            result, report = self.run_interposed(
                box,
                PLANT_VERSION_FILE_HOOK,
                job=CHANGED_JOB,
                environment={"VERSION_PATH": str(version)},
            )

            self.assertEqual(
                version.read_bytes(),
                PLANTED_VERSION_BYTES,
                "the version filename must be created exclusively, so a concurrent "
                "create is reported rather than silently overwritten",
            )
            self.assert_rejected(result, report, "snapshot_version_collision", None)

    def test_an_unexpected_internal_fault_still_prints_one_report(self):
        with self.sandbox() as box:
            result, report = self.run_interposed(box, RAISE_AN_UNEXPECTED_FAULT_HOOK)

            self.assert_rejected(result, report, "snapshot_failed", "job")
            self.assertEqual(self.codes(report["errors"]), ["snapshot_failed"])
            self.assertIsNone(report["canonical_job_id"])
            self.assertIsNone(report["content_digest"])
            self.assertEqual(self.snapshot_names(box), [])


OUT_OF_RANGE_TIMESTAMPS = (
    ("year_one_with_a_positive_offset", "0001-01-01T00:00:00+10:00"),
    ("year_nine_thousand_with_a_negative_offset", "9999-12-31T23:59:59-10:00"),
    ("offset_minutes_over_fifty_nine", "2026-09-11T07:00:54+23:60"),
)


class OutOfRangeTimestampTests(SaveJobSnapshotTestCase):
    """An instant that cannot be converted to UTC must be named as a bad
    timestamp, not swallowed by the catch-all as 'could not be saved'."""

    def test_out_of_range_retrieved_at_names_the_timestamp(self):
        companions = {
            "0001-01-01T00:00:00+10:00": "2026-09-11T07:00:54Z",
            "9999-12-31T23:59:59-10:00": "9999-12-31T23:59:59-10:00",
            "2026-09-11T07:00:54+23:60": "2026-12-01T00:00:00Z",
        }
        for label, value in OUT_OF_RANGE_TIMESTAMPS:
            with self.subTest(retrieved_at=label):
                with self.sandbox() as box:
                    self.save_expecting_rejection(
                        box,
                        "retrieved_at_invalid",
                        "retrieved_at",
                        job=job_with(
                            retrieved_at=value,
                            last_verified_at=companions[value],
                        ),
                    )
                    self.assertEqual(self.snapshot_names(box), [])

    def test_out_of_range_last_verified_at_names_the_timestamp(self):
        for label, value in OUT_OF_RANGE_TIMESTAMPS:
            with self.subTest(last_verified_at=label):
                with self.sandbox() as box:
                    self.save_expecting_rejection(
                        box,
                        "last_verified_at_invalid",
                        "last_verified_at",
                        job=job_with(
                            retrieved_at="2026-01-01T00:00:00Z",
                            last_verified_at=value,
                        ),
                    )
                    self.assertEqual(self.snapshot_names(box), [])


CHANGED_JOB_VERIFIED_LATER = job_with(
    retrieved_at="2026-09-11T07:00:54Z",
    last_verified_at="2026-09-30T12:34:56Z",
    job_description="We are hiring TWO backend engineers in Melbourne.\n",
)


class VersionStampAndAtomicityTests(SaveJobSnapshotTestCase):
    def create_base(self, box):
        report = self.save_ok(box, job=BASE_JOB)
        self.assertEqual(report["status"], "created")
        path = self.snapshot_file(box, report)
        return path, path.read_bytes()

    def test_the_version_stamp_comes_from_retrieved_at(self):
        with self.sandbox() as box:
            base, base_bytes = self.create_base(box)
            self.pin_mtime(base)
            self.reference_in_applications_csv(box, BASE_RELATIVE_PATH)

            report = self.save_ok(box, job=CHANGED_JOB_VERIFIED_LATER)

            self.assertEqual(report["status"], "versioned")
            self.assertEqual(
                report["jd_snapshot_path"],
                VERSIONED_RELATIVE_PATH,
                "the version filename stamps when the advertisement was retrieved, "
                "not when it was last verified",
            )
            self.assertEqual(
                self.snapshot_names(box),
                ["job-1--20260911T070054Z.md", "job-1.md"],
            )
            self.assert_untouched(base, base_bytes)
            frontmatter, _body = self.read_snapshot(self.snapshot_file(box, report))
            self.assertEqual(frontmatter["last_verified_at"], "2026-09-30T12:34:56Z")
            self.assertEqual(frontmatter["retrieved_at"], "2026-09-11T07:00:54Z")

    def test_an_update_renames_a_fresh_file_over_the_old_one(self):
        with self.sandbox() as box:
            base, _base_bytes = self.create_base(box)
            original_inode = base.stat().st_ino

            report = self.save_ok(box, job=CHANGED_JOB)

            self.assertEqual(report["status"], "updated")
            self.assertNotEqual(
                base.stat().st_ino,
                original_inode,
                "an update must rename a completed temporary file into place, so a "
                "reader never sees a half-written snapshot",
            )


class SnapshotFailureTests(SaveJobSnapshotTestCase):
    def create_base(self, box):
        report = self.save_ok(box, job=BASE_JOB)
        self.assertEqual(report["status"], "created")
        path = self.snapshot_file(box, report)
        self.pin_mtime(path)
        return path, path.read_bytes()

    @unittest.skipIf(RUNNING_AS_ROOT, "root bypasses file permissions")
    def test_an_unreadable_existing_snapshot_is_reported(self):
        with self.sandbox() as box:
            base, base_bytes = self.create_base(box)
            os.chmod(base, 0o000)
            try:
                _result, report = self.save_expecting_rejection(
                    box, "snapshot_unreadable", "jd_snapshot_path", job=CHANGED_JOB
                )
                self.assertEqual(self.codes(report["errors"]), ["snapshot_unreadable"])
            finally:
                os.chmod(base, 0o600)

            self.assert_untouched(base, base_bytes)

    @unittest.skipIf(resource is None, "RLIMIT_FSIZE is unavailable here")
    @unittest.skipIf(RUNNING_AS_ROOT, "root bypasses resource limits on some systems")
    def test_a_write_failing_after_the_temporary_file_exists_cleans_up(self):
        with self.sandbox() as box:
            job_path = self.write_job(
                box, job=job_with(job_description=OVERSIZE_DESCRIPTION)
            )
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--workspace",
                    str(box.workspace),
                    "--job",
                    str(job_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
                preexec_fn=limit_written_file_size,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            report = self.parse_report(result, secrets=box.secrets())

            self.assert_rejected(
                result, report, "snapshot_write_failed", "jd_snapshot_path"
            )
            self.assertEqual(
                sorted(path.name for path in box.snapshots.iterdir()),
                [],
                "a write that fails after the temporary file exists must leave no "
                "temporary file and no stray dotfile behind",
            )


class RejectionIdentityTests(SaveJobSnapshotTestCase):
    """A caller logging a rejection wants to know which record was refused, so
    canonical_job_id and content_digest are populated whenever they are known
    and null only when the record never validated."""

    def assert_identity_present(self, report, canonical_job_id="job-1"):
        self.assertEqual(report["canonical_job_id"], canonical_job_id)
        self.assertRegex(report["content_digest"], DIGEST_PATTERN)

    def test_an_alias_conflict_names_the_record(self):
        with self.sandbox() as box:
            self.save_ok(box, job=job_with(canonical_job_id="JOB-1"))

            _result, report = self.save_expecting_rejection(
                box,
                "canonical_job_id_alias_conflict",
                "canonical_job_id",
                job=job_with(canonical_job_id="job-1"),
            )

            self.assert_identity_present(report)

    def test_a_version_collision_names_the_record(self):
        with self.sandbox() as box:
            self.save_ok(box, job=BASE_JOB)
            self.reference_in_applications_csv(box, BASE_RELATIVE_PATH)
            occupied = box.workspace / VERSIONED_RELATIVE_PATH
            occupied.write_bytes(b"an unrelated earlier version\n")

            _result, report = self.save_expecting_rejection(
                box, "snapshot_version_collision", None, job=CHANGED_JOB
            )

            self.assert_identity_present(report)

    def test_an_unsafe_snapshot_path_names_the_record(self):
        with self.sandbox() as box:
            target = box.outside / "leak.md"
            target.write_text("private notes\n", encoding="utf-8")
            (box.snapshots / "job-1.md").symlink_to(target)

            _result, report = self.save_expecting_rejection(
                box, "snapshot_path_unsafe", None
            )

            self.assert_identity_present(report)

    def test_a_record_that_never_validated_stays_unnamed(self):
        cases = (
            ("bad_timestamp", job_with(retrieved_at="not-a-date")),
            ("missing_field", job_without("company")),
        )
        expected = {
            "bad_timestamp": ("retrieved_at_invalid", "retrieved_at"),
            "missing_field": ("required_field_missing", "company"),
        }
        for label, job in cases:
            with self.subTest(rejection=label):
                with self.sandbox() as box:
                    code, field = expected[label]
                    _result, report = self.save_expecting_rejection(
                        box, code, field, job=job
                    )

                    self.assertIsNone(report["canonical_job_id"])
                    self.assertIsNone(report["content_digest"])


class SnapshotFileModeTests(SaveJobSnapshotTestCase):
    """Snapshots hold candidate-facing job data, so they are private (0600)
    rather than the 0644 the rest of the workspace uses.  Pinned so the mode
    is a decision rather than a side effect of ``tempfile.mkstemp``."""

    def assert_private(self, path):
        self.assertEqual(
            stat.S_IMODE(path.stat().st_mode),
            0o600,
            f"{path.name} must be readable and writable only by its owner",
        )

    def test_created_updated_and_versioned_snapshots_are_private(self):
        with self.sandbox() as box:
            created = self.save_ok(box, job=BASE_JOB)
            base = self.snapshot_file(box, created)
            self.assert_private(base)

            os.chmod(base, 0o644)
            updated = self.save_ok(box, job=REVERIFIED_JOB)
            self.assertEqual(updated["status"], "updated")
            self.assert_private(base)

            self.reference_in_applications_csv(box, BASE_RELATIVE_PATH)
            versioned = self.save_ok(box, job=CHANGED_JOB)
            self.assertEqual(versioned["status"], "versioned")
            self.assert_private(self.snapshot_file(box, versioned))


class FixtureTests(SaveJobSnapshotTestCase):
    def load_fixture(self):
        self.assertTrue(FIXTURE.is_file(), f"missing fixture: {FIXTURE.name}")
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def assert_fixture_is_a_complete_synthetic_australian_record(self, record):
        self.assertIsInstance(record, dict)
        self.assertEqual(set(record), set(REQUIRED_FIELDS))
        for field in REQUIRED_FIELDS:
            with self.subTest(field=field):
                self.assertIsInstance(record[field], str)
                self.assertTrue(record[field].strip(), f"{field} must not be blank")
        self.assertIn("Australia", record["location"])
        self.assertIn("example.com", record["canonical_url"])
        self.assertGreaterEqual(
            len(
                [
                    block
                    for block in record["job_description"].split("\n\n")
                    if block.strip()
                ]
            ),
            2,
            "the fixture job description must be multi-paragraph",
        )

    def test_fixture_as_shipped_saves_into_a_fresh_workspace(self):
        record = self.load_fixture()
        self.assert_fixture_is_a_complete_synthetic_australian_record(record)

        with self.sandbox() as box:
            report = self.save_ok(box, job=record)

            self.assertEqual(report["status"], "created")
            self.assertEqual(report["canonical_job_id"], record["canonical_job_id"])
            self.assertEqual(
                report["jd_snapshot_path"],
                f"jobs/snapshots/{record['canonical_job_id']}.md",
            )
            path = self.snapshot_file(box, report)
            frontmatter, body = self.read_snapshot(path)
            self.assertEqual(body, record["job_description"])
            self.assertEqual(frontmatter["company"], record["company"])
            self.assertEqual(frontmatter["content_digest"], report["content_digest"])
            self.assertEqual(
                self.snapshot_names(box),
                [f"{record['canonical_job_id']}.md"],
            )


if __name__ == "__main__":
    unittest.main()
