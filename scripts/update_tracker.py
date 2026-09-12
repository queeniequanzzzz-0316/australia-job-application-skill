"""Record one application event in a private workspace's tracker.

A run reports exactly one of four outcomes.  ``created`` adds the first row for
a canonical_job_id; ``updated`` replaces that row where it stands; ``unchanged``
writes nothing at all; ``rejected`` writes nothing and explains why.

Status never collapses into a single field.  Five dimensions --
discovery_status, verification_status, screening_status, application_stage and
action_status -- each keep their own closed, pairwise-disjoint vocabulary,
their own column, their own ``*_invalid`` code and their own key inside every
event's from_status/to_status.  A value belonging to another dimension is
refused, because accepting it is exactly how five dimensions become one.

A JD snapshot is the evidence that a role was really read, so every stage from
shortlisting onward is gated on a linked snapshot; the early lifecycle and the
two exits are not, and a role can be recorded and abandoned without inventing
evidence.  The snapshot path Task 5 reported is stored verbatim: a regenerated
or normalised path can drift from the archived bytes it is supposed to cite, so
a non-canonical spelling of a real snapshot is refused rather than repaired.

The run touches two files, so it cannot be atomic and the order is what makes
the surviving failure the recoverable one:

1. validate everything, reading nothing that has not been proved contained;
2. precheck that the CSV row and the last event for this job agree, so a run
   interrupted between the two writes is detected instead of layered on;
3. render the whole CSV and write it to a temporary file, proving the write
   can succeed before any history is committed to;
4. append the event to events.jsonl and fsync it, making history durable;
5. rename the temporary file over applications.csv and fsync the directory.

A state change that lost its event is unrecoverable.  An event whose state
never published is detected by step 2 and can be replayed, so that is the
failure allowed to happen -- and it is reported as a rejection carrying
``event_recorded: true``, never as a success.
"""

import argparse
import csv
import datetime
import io
import json
import os
import pathlib
import posixpath
import re
import stat
import sys
import uuid

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from initialise_workspace import (  # noqa: E402
    UnsafeWorkspacePathError,
    validate_contained_paths,
    validate_private_destination,
)


# -- the vocabularies -------------------------------------------------------

# The one place a dimension is declared: its name, its column position and its
# closed vocabulary.  Every other table below is derived from it, so a new
# dimension cannot be added here and forgotten somewhere else.
STATUS_DIMENSIONS = {
    "discovery_status": ("discovered", "duplicate", "expired", "delisted"),
    "verification_status": (
        "unverified",
        "jd_retrieved",
        "verified",
        "verification_failed",
    ),
    "screening_status": (
        "not_screened",
        "screened_in",
        "screened_out",
        "screening_needs_review",
    ),
    "application_stage": (
        "identified",
        "shortlisted",
        "preparing",
        "ready",
        "submitted",
        "interview",
        "offer",
        "unsuccessful",
        "candidate_withdrew",
    ),
    "action_status": (
        "none",
        "awaiting_candidate",
        "awaiting_employer",
        "follow_up_due",
        "closed",
    ),
}
STATUS_KEYS = tuple(STATUS_DIMENSIONS)
STAGE_FIELD = "application_stage"

EVENT_TYPE_FIELD = "event_type"
EVENT_TYPES = (
    "application_recorded",
    "status_changed",
    "snapshot_linked",
    "documents_updated",
    "note_recorded",
)

# The event type is validated exactly like a dimension, so it joins them here
# rather than growing a second validation path of its own.
VOCABULARIES = {**STATUS_DIMENSIONS, EVENT_TYPE_FIELD: EVENT_TYPES}

# What an insert records for a dimension the record left out: the first value of
# that dimension's vocabulary, which is why each vocabulary above opens with its
# starting state.  Deriving it means a sixth dimension cannot be given a
# vocabulary here and left without an initial value somewhere else.
# application_stage is excluded deliberately: it is required, so an insert can
# never need a default for it.
INITIAL_STATUS = {
    dimension: values[0]
    for dimension, values in STATUS_DIMENSIONS.items()
    if dimension != STAGE_FIELD
}

GATED_STAGES = frozenset(
    ("shortlisted", "preparing", "ready", "submitted", "interview", "offer")
)

# Reaching a stage stamps the column that records when it was reached.
STAGE_TIMESTAMP_COLUMNS = {
    "submitted": "applied_at",
    "interview": "interview_at",
    "offer": "offer_at",
}


# -- the record this script accepts -----------------------------------------

IDENTITY_FIELD = "canonical_job_id"
OCCURRED_AT_FIELD = "occurred_at"
SOURCE_FIELD = "source"
SNAPSHOT_PATH_FIELD = "jd_snapshot_path"
DOCUMENT_VERSIONS_FIELD = "document_versions"
RECORD_FIELD = "application"
TRACKER_FIELD = "tracker"
WORKSPACE_FIELD = "workspace"

REQUIRED_FIELDS = (
    IDENTITY_FIELD,
    OCCURRED_AT_FIELD,
    EVENT_TYPE_FIELD,
    SOURCE_FIELD,
    STAGE_FIELD,
)
OPTIONAL_FIELDS = (
    *(key for key in STATUS_KEYS if key != STAGE_FIELD),
    SNAPSHOT_PATH_FIELD,
    DOCUMENT_VERSIONS_FIELD,
)
ACCEPTED_FIELDS = frozenset(REQUIRED_FIELDS + OPTIONAL_FIELDS)


# -- the two tracker files --------------------------------------------------

DESCRIPTIVE_COLUMNS = ("source", "canonical_url", "job_title", "company", "location")
APPLICATION_HEADER = (
    IDENTITY_FIELD,
    SNAPSHOT_PATH_FIELD,
    *DESCRIPTIVE_COLUMNS,
    *STATUS_KEYS,
    "first_seen_at",
    "last_verified_at",
    "applied_at",
    "interview_at",
    "offer_at",
)

TRACKER_DIRECTORY = "tracker"
APPLICATIONS_CSV = "tracker/applications.csv"
EVENTS_JSONL = "tracker/events.jsonl"
TRACKER_FILE_MODE = 0o600
TEMPORARY_PREFIX = "."
TEMPORARY_SUFFIX = ".tmp"


# -- snapshots (Task 5's format, read only) ---------------------------------

SNAPSHOT_FRONTMATTER_KEYS = (
    IDENTITY_FIELD,
    "retrieved_at",
    "last_verified_at",
    *DESCRIPTIVE_COLUMNS,
    "content_digest",
)
DELIMITER_LINE = "---"


# -- limits and patterns ----------------------------------------------------

MAXIMUM_RECORD_BYTES = 1024 * 1024
MAXIMUM_TRACKER_BYTES = 4 * 1024 * 1024
MAXIMUM_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAXIMUM_JOB_ID_LENGTH = 128
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
VERSION_SEPARATOR = "--"
TIMESTAMP_PATTERN = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})[Tt]"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<fraction>\d+))?"
    r"(?P<zone>[Zz]|[+-]\d{2}:\d{2})$"
)
MAXIMUM_OFFSET_HOURS = 23
MAXIMUM_OFFSET_MINUTES = 59

CREATED = "created"
UPDATED = "updated"
UNCHANGED = "unchanged"
REJECTED = "rejected"

ABSENT = object()


class RecordUnreadableError(Exception):
    pass


class RecordMalformedError(Exception):
    pass


class TrackerUnreadableError(Exception):
    pass


class TrackerDuplicateRowError(Exception):
    pass


class TrackerOutOfSyncError(Exception):
    pass


class TrackerWriteError(Exception):
    pass


class TrackerEventError(Exception):
    pass


class TrackerPublishError(Exception):
    pass


class TrackerReport:
    """The single JSON object this script prints, built up as work proceeds."""

    def __init__(self):
        self.errors = []
        self.status = CREATED
        self.canonical_job_id = None
        self.jd_snapshot_path = None
        self.event_id = None
        self.event_recorded = False

    def error(self, code, field, message):
        self.errors.append({"code": code, "field": field, "message": message})

    def record_event(self, event_id):
        self.event_id = event_id
        self.event_recorded = True

    def as_dict(self):
        """A rejection withholds the snapshot path but never the event.

        The path is withheld because the value that failed may be the caller's
        own spelling of somewhere outside the workspace, and no diagnostic may
        disclose a filesystem path.  The event is reported even on a rejection,
        because a publish that failed after the append is only replayable if
        the caller is told the append happened.
        """
        rejected = bool(self.errors)
        return {
            "status": REJECTED if rejected else self.status,
            "canonical_job_id": self.canonical_job_id,
            "jd_snapshot_path": None if rejected else self.jd_snapshot_path,
            "event_id": self.event_id if self.event_recorded else None,
            "event_recorded": self.event_recorded,
            "errors": self.errors,
        }


# -- reading the application record -----------------------------------------


def read_record_text(record_path):
    """Read the record, bounded and only if it is a regular file.

    The size is checked before the read and the node type before that: an
    unbounded read of attacker-influenced data can be killed for memory, and a
    killed process prints no report at all, while opening a FIFO would block
    forever.
    """
    try:
        status = os.stat(record_path)
    except OSError as error:
        raise RecordUnreadableError from error
    if not stat.S_ISREG(status.st_mode) or status.st_size > MAXIMUM_RECORD_BYTES:
        raise RecordUnreadableError
    try:
        return record_path.read_text(encoding="utf-8")
    except (OSError, ValueError) as error:
        raise RecordUnreadableError from error


def load_record(record_path):
    contents = read_record_text(record_path)
    try:
        return json.loads(contents)
    except (ValueError, RecursionError) as error:
        raise RecordMalformedError from error


def load_validated_record(record_path, report):
    try:
        record = load_record(record_path)
    except RecordUnreadableError:
        report.error(
            "application_record_unreadable",
            RECORD_FIELD,
            "The application record file could not be read.",
        )
        return None
    except RecordMalformedError:
        report.error(
            "application_record_malformed",
            RECORD_FIELD,
            "The application record file is not valid JSON.",
        )
        return None
    if not isinstance(record, dict):
        report.error(
            "application_record_not_a_mapping",
            RECORD_FIELD,
            "The application record must be a JSON object at its top level.",
        )
        return None
    return record


# -- validating the application record --------------------------------------


def is_blank_text(value):
    return not isinstance(value, str) or not value.strip()


def is_safe_canonical_job_id(value):
    return (
        isinstance(value, str)
        and len(value) <= MAXIMUM_JOB_ID_LENGTH
        and JOB_ID_PATTERN.match(value) is not None
    )


def is_reserved_canonical_job_id(value):
    """``--`` separates a snapshot identifier from its version stamp, so an
    identifier containing it could name another identifier's archived file."""
    return isinstance(value, str) and VERSION_SEPARATOR in value


def parse_zone(zone):
    if zone in ("Z", "z"):
        return datetime.timezone.utc
    hours = int(zone[1:3])
    minutes = int(zone[4:6])
    if hours > MAXIMUM_OFFSET_HOURS or minutes > MAXIMUM_OFFSET_MINUTES:
        return None
    offset = datetime.timedelta(hours=hours, minutes=minutes)
    return datetime.timezone(-offset if zone[0] == "-" else offset)


def parse_instant(value):
    """Return an RFC 3339 instant normalised to UTC, or None.

    The conversion to UTC happens here so an instant near the edge of the
    representable range is reported as a bad timestamp rather than raising out
    of whatever later code first converts it.
    """
    if not isinstance(value, str):
        return None
    match = TIMESTAMP_PATTERN.match(value)
    if match is None:
        return None
    zone = parse_zone(match.group("zone"))
    if zone is None:
        return None
    try:
        instant = datetime.datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
            int((match.group("fraction") or "")[:6].ljust(6, "0")),
            tzinfo=zone,
        )
        return instant.astimezone(datetime.timezone.utc)
    except (ValueError, OverflowError):
        return None


def validate_canonical_job_id(value, report):
    if not is_safe_canonical_job_id(value):
        report.error(
            "canonical_job_id_unsafe",
            IDENTITY_FIELD,
            "canonical_job_id must be 1 to 128 characters of letters, digits, "
            "dots, underscores or hyphens and must start with a letter or digit.",
        )
    if is_reserved_canonical_job_id(value):
        report.error(
            "canonical_job_id_reserved",
            IDENTITY_FIELD,
            "canonical_job_id must not contain '--', which is reserved to "
            "separate an identifier from its version stamp.",
        )


def validate_vocabularies(record, report):
    """One closed vocabulary per dimension, so a value belonging to another
    dimension is refused for this one."""
    for field, vocabulary in VOCABULARIES.items():
        if field in record and record[field] not in vocabulary:
            report.error(
                f"{field}_invalid",
                field,
                f"{field} must be one of the values this skill defines for it.",
            )


def validate_record(record, report):
    for field in sorted(set(record) - ACCEPTED_FIELDS):
        report.error(
            "unrecognised_field_rejected",
            field,
            "The application record contains a field this skill does not accept.",
        )
    for field in REQUIRED_FIELDS:
        if field not in record:
            report.error(
                "required_field_missing",
                field,
                f"{field} is required in the application record.",
            )
    if IDENTITY_FIELD in record:
        validate_canonical_job_id(record[IDENTITY_FIELD], report)
    if OCCURRED_AT_FIELD in record and parse_instant(record[OCCURRED_AT_FIELD]) is None:
        report.error(
            "occurred_at_invalid",
            OCCURRED_AT_FIELD,
            "occurred_at must be an RFC 3339 instant with a UTC offset.",
        )
    if SOURCE_FIELD in record and is_blank_text(record[SOURCE_FIELD]):
        report.error(
            "required_field_invalid",
            SOURCE_FIELD,
            "source must be a non-empty string.",
        )
    validate_vocabularies(record, report)
    if DOCUMENT_VERSIONS_FIELD in record and not isinstance(
        record[DOCUMENT_VERSIONS_FIELD], dict
    ):
        report.error(
            "document_versions_invalid",
            DOCUMENT_VERSIONS_FIELD,
            "document_versions must be a JSON object.",
        )


# -- the workspace ----------------------------------------------------------


def resolve_workspace(workspace_argument):
    workspace = validate_private_destination(workspace_argument)
    try:
        if workspace.is_symlink() or not workspace.is_dir():
            raise UnsafeWorkspacePathError
    except UnsafeWorkspacePathError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise UnsafeWorkspacePathError from error
    return workspace


def validate_tracker_layout(workspace):
    """Prove the tracker directory and both files before anything reads them."""
    directory = workspace / TRACKER_DIRECTORY
    files = [workspace / APPLICATIONS_CSV, workspace / EVENTS_JSONL]
    validate_contained_paths(workspace, [directory, *files])
    try:
        if not directory.is_dir():
            raise UnsafeWorkspacePathError
        for path in files:
            if path.exists() and not path.is_file():
                raise UnsafeWorkspacePathError
    except UnsafeWorkspacePathError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise UnsafeWorkspacePathError from error


# -- the linked JD snapshot -------------------------------------------------


def is_canonical_relative_path(value):
    """The path must be the exact workspace-relative spelling Task 5 reported.

    Anything that merely denotes the same file -- a leading ``./``, a doubled
    separator, an interior ``.`` or ``..``, backslashes, a trailing slash,
    surrounding whitespace, an absolute path -- is a different string, and a
    stored citation that is not the string the saver minted can drift from the
    bytes it cites.  Such a spelling is refused, never normalised.
    """
    return (
        isinstance(value, str)
        and value != ""
        and value == value.strip()
        and "\\" not in value
        and not posixpath.isabs(value)
        and posixpath.normpath(value) == value
    )


def parse_frontmatter(text):
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != DELIMITER_LINE:
        return None
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") != DELIMITER_LINE:
            continue
        try:
            document = yaml.safe_load("".join(lines[1:index]))
        except (yaml.YAMLError, RecursionError):
            return None
        return document if isinstance(document, dict) else None
    return None


def read_snapshot_frontmatter(snapshot_path):
    """Return the frontmatter of a contained snapshot, or None.

    ``lstat`` rather than ``stat``: containment has already refused every
    symlink on the way here, and reading the link's own metadata keeps it that
    way even if the tree changes underneath.
    """
    try:
        status = os.lstat(snapshot_path)
    except OSError:
        return None
    if not stat.S_ISREG(status.st_mode) or status.st_size > MAXIMUM_SNAPSHOT_BYTES:
        return None
    try:
        text = snapshot_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    return parse_frontmatter(text)


def is_complete_frontmatter(frontmatter):
    return all(
        isinstance(frontmatter.get(key), str) for key in SNAPSHOT_FRONTMATTER_KEYS
    )


def link_snapshot(workspace, canonical_job_id, value, report):
    """Validate the snapshot a record cites and return its frontmatter.

    Containment is decided before the file is opened, so a symlink pointing out
    of the workspace is refused rather than followed.
    """
    if not is_canonical_relative_path(value):
        report.error(
            "jd_snapshot_path_not_canonical",
            SNAPSHOT_PATH_FIELD,
            "jd_snapshot_path must be the workspace-relative snapshot path "
            "exactly as the snapshot saver reported it.",
        )
        return None

    snapshot_path = workspace / value
    try:
        validate_contained_paths(workspace, [snapshot_path])
    except UnsafeWorkspacePathError:
        report.error(
            "jd_snapshot_path_unsafe",
            SNAPSHOT_PATH_FIELD,
            "The snapshot path is not contained in the workspace.",
        )
        return None

    if not snapshot_path.exists():
        report.error(
            "jd_snapshot_missing",
            SNAPSHOT_PATH_FIELD,
            "No snapshot exists at the cited path in this workspace.",
        )
        return None

    frontmatter = read_snapshot_frontmatter(snapshot_path)
    if (
        frontmatter is None
        or not is_complete_frontmatter(frontmatter)
        or frontmatter[IDENTITY_FIELD] != canonical_job_id
    ):
        report.error(
            "jd_snapshot_invalid",
            SNAPSHOT_PATH_FIELD,
            "The cited snapshot is not a complete snapshot of this job.",
        )
        return None
    return frontmatter


# -- reading the two tracker files ------------------------------------------


def open_tracker_file(path):
    """Open a tracker file for streaming, or return None when it is absent.

    A tracker larger than the cap is refused rather than read, for the same
    reason the record is bounded: a process killed for memory prints no report.
    """
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise TrackerUnreadableError from error
    if not stat.S_ISREG(status.st_mode) or status.st_size > MAXIMUM_TRACKER_BYTES:
        raise TrackerUnreadableError
    try:
        return open(path, "r", encoding="utf-8", newline="")
    except OSError as error:
        raise TrackerUnreadableError from error


def read_application_rows(path):
    handle = open_tracker_file(path)
    if handle is None:
        return []
    try:
        with handle:
            raw_rows = list(csv.reader(handle))
    except (OSError, ValueError, csv.Error, RecursionError) as error:
        raise TrackerUnreadableError from error
    if not raw_rows:
        return []
    if tuple(raw_rows[0]) != APPLICATION_HEADER:
        raise TrackerUnreadableError
    rows = []
    for raw_row in raw_rows[1:]:
        if not raw_row:
            continue
        if len(raw_row) != len(APPLICATION_HEADER):
            raise TrackerUnreadableError
        rows.append(dict(zip(APPLICATION_HEADER, raw_row)))
    return rows


def read_events(path):
    handle = open_tracker_file(path)
    if handle is None:
        return []
    events = []
    try:
        with handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise TrackerUnreadableError
                events.append(event)
    except TrackerUnreadableError:
        raise
    except (OSError, ValueError, RecursionError) as error:
        raise TrackerUnreadableError from error
    return events


def duplicated_identifiers(rows):
    seen = set()
    duplicates = set()
    for row in rows:
        identifier = row[IDENTITY_FIELD]
        if identifier in seen:
            duplicates.add(identifier)
        seen.add(identifier)
    return duplicates


def row_for(rows, canonical_job_id):
    for row in rows:
        if row[IDENTITY_FIELD] == canonical_job_id:
            return row
    return None


def last_recorded_status(events, canonical_job_id):
    for event in reversed(events):
        if event.get(IDENTITY_FIELD) == canonical_job_id:
            return event.get("to_status")
    return ABSENT


def current_status(row):
    return {key: row[key] for key in STATUS_KEYS}


class TrackerState:
    """The two files as this run found them, for one canonical_job_id."""

    def __init__(self, rows, events, canonical_job_id):
        self.rows = rows
        self.canonical_job_id = canonical_job_id
        self.row = row_for(rows, canonical_job_id)
        self.recorded_status = last_recorded_status(events, canonical_job_id)

    @property
    def status(self):
        return None if self.row is None else current_status(self.row)

    @property
    def snapshot_path(self):
        return "" if self.row is None else self.row[SNAPSHOT_PATH_FIELD]

    def agrees(self):
        """Do the CSV row and the last event for this job say the same thing?

        Disagreement means a previous run died between the two writes, so this
        run must fail closed rather than layer a second change on divergent
        state.  Only this job is compared: another job's divergence is that
        job's problem to replay.
        """
        if self.row is None:
            return self.recorded_status is ABSENT
        if not isinstance(self.recorded_status, dict):
            return False
        return all(self.recorded_status.get(key) == self.row[key] for key in STATUS_KEYS)


def read_tracker_state(workspace, canonical_job_id):
    rows = read_application_rows(workspace / APPLICATIONS_CSV)
    if duplicated_identifiers(rows):
        raise TrackerDuplicateRowError
    events = read_events(workspace / EVENTS_JSONL)
    state = TrackerState(rows, events, canonical_job_id)
    if not state.agrees():
        raise TrackerOutOfSyncError
    return state


# -- planning the change ----------------------------------------------------


def next_status(record, state):
    """A dimension the record omits keeps its current value, or takes its
    documented initial value when there is no row yet."""
    status = {}
    for key in STATUS_KEYS:
        if key in record:
            status[key] = record[key]
        elif state.row is not None:
            status[key] = state.row[key]
        else:
            status[key] = INITIAL_STATUS[key]
    return status


def next_snapshot_path(record, state):
    linked = record.get(SNAPSHOT_PATH_FIELD)
    return state.snapshot_path if linked is None else linked


def build_row(record, state, status, snapshot_path, frontmatter):
    """The row this run will publish, built from the row it found."""
    row = (
        dict(state.row)
        if state.row is not None
        else {column: "" for column in APPLICATION_HEADER}
    )
    row[IDENTITY_FIELD] = state.canonical_job_id
    row[SNAPSHOT_PATH_FIELD] = snapshot_path
    row.update(status)
    if frontmatter is not None:
        for column in DESCRIPTIVE_COLUMNS:
            row[column] = frontmatter[column]
        row["last_verified_at"] = frontmatter["last_verified_at"]
    if not row["first_seen_at"]:
        row["first_seen_at"] = record[OCCURRED_AT_FIELD]
    stage_column = STAGE_TIMESTAMP_COLUMNS.get(status[STAGE_FIELD])
    if stage_column is not None and not row[stage_column]:
        row[stage_column] = record[OCCURRED_AT_FIELD]
    return row


def replace_row(rows, canonical_job_id, row):
    """Rewrite the row where it stands; a new job appends at the end."""
    for index, existing in enumerate(rows):
        if existing[IDENTITY_FIELD] == canonical_job_id:
            return [*rows[:index], row, *rows[index + 1 :]]
    return [*rows, row]


def build_event(record, state, status, snapshot_path):
    return {
        "event_id": uuid.uuid4().hex,
        OCCURRED_AT_FIELD: record[OCCURRED_AT_FIELD],
        IDENTITY_FIELD: state.canonical_job_id,
        EVENT_TYPE_FIELD: record[EVENT_TYPE_FIELD],
        "from_status": state.status,
        "to_status": status,
        SNAPSHOT_PATH_FIELD: snapshot_path or None,
        DOCUMENT_VERSIONS_FIELD: record.get(DOCUMENT_VERSIONS_FIELD, {}),
        SOURCE_FIELD: record[SOURCE_FIELD],
    }


class Change:
    """A planned change, or the absence of one, decided before any write."""

    def __init__(self, state, status, snapshot_path, rows=None, event=None):
        self.state = state
        self.status = status
        self.snapshot_path = snapshot_path
        self.rows = rows
        self.event = event

    @property
    def is_noop(self):
        return self.event is None

    @property
    def outcome(self):
        if self.is_noop:
            return UNCHANGED
        return CREATED if self.state.row is None else UPDATED


def plan_change(record, state, frontmatter):
    """Decide the whole change without touching a single byte on disk.

    A run whose five dimensions and snapshot path all match the stored row is
    a no-op: it writes neither file and records no event, because history is
    the record of state changing and nothing changed.
    """
    status = next_status(record, state)
    snapshot_path = next_snapshot_path(record, state)
    if state.status == status and state.snapshot_path == snapshot_path:
        return Change(state, status, snapshot_path)
    row = build_row(record, state, status, snapshot_path, frontmatter)
    return Change(
        state,
        status,
        snapshot_path,
        rows=replace_row(state.rows, state.canonical_job_id, row),
        event=build_event(record, state, status, snapshot_path),
    )


# -- writing the two files --------------------------------------------------


def render_applications(rows):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(APPLICATION_HEADER)
    for row in rows:
        writer.writerow([row[column] for column in APPLICATION_HEADER])
    return buffer.getvalue().encode("utf-8")


def remove_quietly(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def write_temporary(directory, payload):
    """Write the completed replacement beside its target, fsynced but unnamed."""
    path = directory / (TEMPORARY_PREFIX + os.urandom(8).hex() + TEMPORARY_SUFFIX)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, TRACKER_FILE_MODE
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        remove_quietly(path)
        raise
    return path


def append_event(events_path, event):
    """Append one line under O_APPEND and fsync it.

    O_APPEND rather than a rewrite because history that can be rewritten cannot
    be replayed against, and the fsync because the whole point of writing the
    event first is that it is durable before the state it describes publishes.
    """
    payload = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    descriptor = os.open(
        events_path,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
        TRACKER_FILE_MODE,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def publish(temporary_path, applications_path, directory):
    os.replace(temporary_path, applications_path)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def commit_change(workspace, change, report):
    """Prove the CSV write, make the event durable, then publish the state.

    The temporary file is removed on every failure path, so a rejected or
    interrupted run never leaves a partial file or a stray dotfile behind.
    """
    directory = workspace / TRACKER_DIRECTORY
    payload = render_applications(change.rows)
    try:
        temporary_path = write_temporary(directory, payload)
    except OSError as error:
        raise TrackerWriteError from error
    try:
        try:
            append_event(workspace / EVENTS_JSONL, change.event)
        except OSError as error:
            raise TrackerEventError from error
        report.record_event(change.event["event_id"])
        try:
            publish(temporary_path, workspace / APPLICATIONS_CSV, directory)
        except OSError as error:
            raise TrackerPublishError from error
    except BaseException:
        remove_quietly(temporary_path)
        raise


# -- diagnostics ------------------------------------------------------------

# Every way the tracker phases can fail, each with the diagnostic a caller
# sees.  Keeping them in one table is what lets build_report read as
# validate -> precheck -> plan -> write.  Messages stay generic: a diagnostic
# must never disclose a filesystem path.
TRACKER_DIAGNOSTICS = (
    (
        TrackerDuplicateRowError,
        "tracker_duplicate_row",
        IDENTITY_FIELD,
        "The tracker holds more than one row for a job, so no row can be "
        "updated without guessing which one is current.",
    ),
    (
        TrackerOutOfSyncError,
        "tracker_out_of_sync",
        TRACKER_FIELD,
        "The tracker row and the event log disagree about this job, so an "
        "earlier run did not finish; replay it before recording more.",
    ),
    (
        TrackerUnreadableError,
        "tracker_unreadable",
        TRACKER_FIELD,
        "The tracker files could not be read as this skill writes them.",
    ),
    (
        TrackerEventError,
        "tracker_event_write_failed",
        TRACKER_FIELD,
        "The event could not be appended to the tracker history.",
    ),
    (
        TrackerPublishError,
        "tracker_publish_failed",
        TRACKER_FIELD,
        "The event was recorded but the tracker state could not be published; "
        "the recorded event can be replayed.",
    ),
    (
        TrackerWriteError,
        "tracker_write_failed",
        TRACKER_FIELD,
        "The tracker state could not be written into the workspace.",
    ),
    (
        UnsafeWorkspacePathError,
        "workspace_unsafe",
        WORKSPACE_FIELD,
        "The workspace must be an existing private directory outside this skill.",
    ),
    (
        OSError,
        "tracker_write_failed",
        TRACKER_FIELD,
        "The tracker state could not be written into the workspace.",
    ),
)

TRACKER_FAULTS = tuple(entry[0] for entry in TRACKER_DIAGNOSTICS)


def diagnose(fault):
    for fault_type, code, field, message in TRACKER_DIAGNOSTICS:
        if isinstance(fault, fault_type):
            return code, field, message
    raise fault


# -- orchestration ----------------------------------------------------------


def validate_gate(status, snapshot_path, report):
    """Evidence, not optimism: a stage from shortlisting onward needs a linked
    snapshot, while the early lifecycle and the two exits do not.

    The gate is the one check that reads the planned state rather than the
    record, because a snapshot linked by an earlier run keeps later gated
    stages open.  So it runs after planning -- and still before any write.
    """
    if status[STAGE_FIELD] in GATED_STAGES and not snapshot_path:
        report.error(
            "jd_snapshot_required",
            SNAPSHOT_PATH_FIELD,
            "This application stage requires a linked job description snapshot.",
        )


def build_report(workspace_argument, record_argument):
    """Validate, precheck, plan, write: nothing is mutated before the plan."""
    report = TrackerReport()
    try:
        workspace = resolve_workspace(workspace_argument)
        validate_tracker_layout(workspace)
    except UnsafeWorkspacePathError as fault:
        report.error(*diagnose(fault))
        return report

    record = load_validated_record(record_argument, report)
    if record is None:
        return report
    validate_record(record, report)
    if report.errors:
        return report

    canonical_job_id = record[IDENTITY_FIELD]
    frontmatter = None
    if SNAPSHOT_PATH_FIELD in record:
        frontmatter = link_snapshot(
            workspace, canonical_job_id, record[SNAPSHOT_PATH_FIELD], report
        )
        if report.errors:
            return report

    # The record has validated, so any rejection from here on can name the job.
    report.canonical_job_id = canonical_job_id
    try:
        state = read_tracker_state(workspace, canonical_job_id)
    except TRACKER_FAULTS as fault:
        report.error(*diagnose(fault))
        return report

    change = plan_change(record, state, frontmatter)
    validate_gate(change.status, change.snapshot_path, report)
    if report.errors:
        return report

    if not change.is_noop:
        try:
            commit_change(workspace, change, report)
        except TRACKER_FAULTS as fault:
            report.error(*diagnose(fault))
            return report

    report.status = change.outcome
    report.jd_snapshot_path = change.snapshot_path or None
    return report


def failure_report():
    report = TrackerReport()
    report.error(
        "tracker_update_failed",
        RECORD_FIELD,
        "The tracker could not be updated.",
    )
    return report


def emit(report):
    payload = report.as_dict()
    print(json.dumps(payload))
    return 1 if payload["errors"] else 0


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Record an application event in a private workspace tracker."
    )
    parser.add_argument(
        "--workspace",
        required=True,
        type=pathlib.Path,
        help="Path to the private workspace that owns the tracker.",
    )
    parser.add_argument(
        "--input-json",
        required=True,
        type=pathlib.Path,
        help="Path to the JSON application record to record.",
    )
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    try:
        exit_code = emit(build_report(arguments.workspace, arguments.input_json))
    except Exception:
        exit_code = emit(failure_report())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
