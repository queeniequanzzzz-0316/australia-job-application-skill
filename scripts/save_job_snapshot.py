"""Save a job advertisement into a private workspace as a durable snapshot.

A save reports exactly one of four outcomes.  ``created`` writes a snapshot
that did not exist; ``unchanged`` writes nothing at all; ``updated`` rewrites
the base snapshot ``jobs/snapshots/<id>.md`` in place; ``versioned`` mints
``jobs/snapshots/<id>--<retrieved_at>.md`` beside the base and leaves the base
byte-for-byte alone.

The rule separating the last two is reference: a snapshot becomes immutable
the moment a tracker file references it.  Until something cites the old bytes,
a material change to an unreferenced base is an in-place update by design.
Because ``--`` separates an identifier from its version stamp, it is reserved
and no canonical_job_id may contain it.

This script only ever reads the tracker.  Writing it belongs to another tool.
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
import pathlib
import posixpath
import re
import stat
import sys
import urllib.parse

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from initialise_workspace import (  # noqa: E402
    UnsafeWorkspacePathError,
    validate_contained_paths,
    validate_private_destination,
)


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

IDENTITY_FIELD = "canonical_job_id"
BODY_FIELD = "job_description"
TIMESTAMP_FIELDS = ("retrieved_at", "last_verified_at")

# Derived from REQUIRED_FIELDS so a new field cannot be added to one tuple and
# silently forgotten in another.  The free-text fields are everything that is
# neither the identifier nor a provenance timestamp; they are also exactly the
# fields the content digest covers, because re-fetching an unchanged
# advertisement must not read as a material change.
TEXT_FIELDS = tuple(
    field
    for field in REQUIRED_FIELDS
    if field != IDENTITY_FIELD and field not in TIMESTAMP_FIELDS
)
MATERIAL_FIELDS = TEXT_FIELDS
FRONTMATTER_FIELDS = tuple(field for field in REQUIRED_FIELDS if field != BODY_FIELD)

SNAPSHOT_DIRECTORY = "jobs/snapshots"
SNAPSHOT_DIRECTORIES = ("jobs", SNAPSHOT_DIRECTORY)
SNAPSHOT_SUFFIX = ".md"
SNAPSHOT_FILE_MODE = 0o600
VERSION_SEPARATOR = "--"
VERSION_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
VERSION_STAMP_PATTERN = re.compile(VERSION_SEPARATOR + r"\d{8}T\d{6}Z$")
TEMPORARY_PREFIX = "."
TEMPORARY_SUFFIX = ".tmp"

APPLICATIONS_CSV = "tracker/applications.csv"
EVENTS_JSONL = "tracker/events.jsonl"
SNAPSHOT_PATH_COLUMN = "jd_snapshot_path"

MAXIMUM_JOB_RECORD_BYTES = 1024 * 1024
MAXIMUM_TRACKER_BYTES = 4 * 1024 * 1024
MAXIMUM_JOB_ID_LENGTH = 128
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
URL_SCHEMES = ("http", "https")
TIMESTAMP_PATTERN = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})[Tt]"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.(?P<fraction>\d+))?"
    r"(?P<zone>[Zz]|[+-]\d{2}:\d{2})$"
)
MAXIMUM_OFFSET_HOURS = 23
MAXIMUM_OFFSET_MINUTES = 59

DELIMITER_LINE = "---"
DELIMITER = DELIMITER_LINE + "\n"
YAML_WIDTH = 1_000_000
DIGEST_PREFIX = "sha256:"

CREATED = "created"
UNCHANGED = "unchanged"
UPDATED = "updated"
VERSIONED = "versioned"
REJECTED = "rejected"


class JobRecordUnreadableError(Exception):
    pass


class JobRecordMalformedError(Exception):
    pass


class TrackerUnreadableError(Exception):
    pass


class SnapshotUnreadableError(Exception):
    pass


class SnapshotCollisionError(Exception):
    pass


class AliasConflictError(Exception):
    pass


class SnapshotReport:
    """The single JSON object this script prints, built up as work proceeds."""

    def __init__(self):
        self.errors = []
        self.status = CREATED
        self.snapshot_path = None
        self.canonical_job_id = None
        self.content_digest = None

    def error(self, code, field, message):
        self.errors.append({"code": code, "field": field, "message": message})

    def as_dict(self):
        rejected = bool(self.errors)
        return {
            "status": REJECTED if rejected else self.status,
            "jd_snapshot_path": None if rejected else self.snapshot_path,
            "canonical_job_id": self.canonical_job_id,
            "content_digest": self.content_digest,
            "errors": self.errors,
        }


# -- reading the job record ------------------------------------------------


def read_job_record_text(job_path):
    try:
        status = os.stat(job_path)
    except OSError as error:
        raise JobRecordUnreadableError from error
    if not stat.S_ISREG(status.st_mode) or status.st_size > MAXIMUM_JOB_RECORD_BYTES:
        raise JobRecordUnreadableError
    try:
        return job_path.read_text(encoding="utf-8")
    except (OSError, ValueError) as error:
        raise JobRecordUnreadableError from error


def load_job_record(job_path):
    contents = read_job_record_text(job_path)
    try:
        return json.loads(contents)
    except (ValueError, RecursionError) as error:
        raise JobRecordMalformedError from error


# -- validating the job record ---------------------------------------------


def is_blank_text(value):
    return not isinstance(value, str) or not value.strip()


def is_safe_canonical_job_id(value):
    return (
        isinstance(value, str)
        and len(value) <= MAXIMUM_JOB_ID_LENGTH
        and JOB_ID_PATTERN.match(value) is not None
    )


def is_reserved_canonical_job_id(value):
    """``--`` separates an identifier from its version stamp, so an identifier
    containing it could name the version file of a different identifier and
    overwrite an archived snapshot other records already cite."""
    return isinstance(value, str) and VERSION_SEPARATOR in value


def is_valid_canonical_url(value):
    """Only http(s) with a real host: the value is copied verbatim into the
    frontmatter and later rendered as a link, so file: and javascript: URLs
    must never reach storage."""
    if not isinstance(value, str):
        return False
    try:
        parts = urllib.parse.urlsplit(value.strip())
        if parts.scheme.lower() not in URL_SCHEMES:
            return False
        return bool(parts.hostname)
    except ValueError:
        return False


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


def validate_fields(record, report):
    for field in sorted(set(record) - set(REQUIRED_FIELDS)):
        report.error(
            "unrecognised_field_rejected",
            field,
            "The job record contains a field this skill does not accept.",
        )
    for field in REQUIRED_FIELDS:
        if field not in record:
            report.error(
                "required_field_missing",
                field,
                f"{field} is required in the job record.",
            )
    for field in TEXT_FIELDS:
        if field in record and is_blank_text(record[field]):
            report.error(
                "required_field_invalid",
                field,
                f"{field} must be a non-empty string.",
            )
    if "canonical_url" in record and not is_blank_text(record["canonical_url"]):
        if not is_valid_canonical_url(record["canonical_url"]):
            report.error(
                "canonical_url_invalid",
                "canonical_url",
                "canonical_url must be an http or https URL with a host.",
            )
    if IDENTITY_FIELD in record:
        validate_canonical_job_id(record[IDENTITY_FIELD], report)


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


def validate_instants(record, report):
    """Record timestamp faults; return the instants that did parse."""
    instants = {}
    for field in TIMESTAMP_FIELDS:
        if field not in record:
            continue
        instant = parse_instant(record[field])
        if instant is None:
            report.error(
                f"{field}_invalid",
                field,
                f"{field} must be an RFC 3339 instant with a UTC offset.",
            )
        else:
            instants[field] = instant
    if len(instants) == len(TIMESTAMP_FIELDS) and (
        instants["last_verified_at"] < instants["retrieved_at"]
    ):
        report.error(
            "last_verified_at_before_retrieved_at",
            "last_verified_at",
            "last_verified_at must not be earlier than retrieved_at.",
        )
    return instants


# -- rendering -------------------------------------------------------------


def compute_content_digest(record):
    """Digest the material fields only, never the provenance timestamps."""
    material = {field: record[field] for field in MATERIAL_FIELDS}
    encoded = json.dumps(
        material, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return DIGEST_PREFIX + hashlib.sha256(encoded).hexdigest()


def render_snapshot(record, content_digest):
    frontmatter = {field: record[field] for field in FRONTMATTER_FIELDS}
    frontmatter["content_digest"] = content_digest
    rendered = yaml.safe_dump(
        frontmatter,
        sort_keys=True,
        allow_unicode=True,
        default_flow_style=False,
        width=YAML_WIDTH,
    )
    body = record[BODY_FIELD]
    return (DELIMITER + rendered + DELIMITER + body).encode("utf-8")


def read_frontmatter(snapshot_path):
    try:
        text = snapshot_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
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


def recorded_content_digest(snapshot_path):
    frontmatter = read_frontmatter(snapshot_path) or {}
    digest = frontmatter.get("content_digest")
    return digest if isinstance(digest, str) else None


# -- workspace and snapshot paths ------------------------------------------


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


def snapshot_relative_path(canonical_job_id, instant=None):
    """The workspace-relative snapshot path, always with forward slashes.

    ``instant`` is the retrieval time and is already UTC, so the stamp needs no
    further conversion.
    """
    name = canonical_job_id
    if instant is not None:
        name = (
            f"{canonical_job_id}{VERSION_SEPARATOR}"
            f"{instant.strftime(VERSION_STAMP_FORMAT)}"
        )
    return f"{SNAPSHOT_DIRECTORY}/{name}{SNAPSHOT_SUFFIX}"


def validate_snapshot_paths(workspace, relative_paths):
    """Refuse escapes, symlinks, wrong node types and hard links before writing."""
    directories = [workspace / relative for relative in SNAPSHOT_DIRECTORIES]
    targets = [workspace / relative for relative in relative_paths]
    validate_contained_paths(workspace, [*directories, *targets])
    try:
        for directory in directories:
            if directory.exists() and not directory.is_dir():
                raise UnsafeWorkspacePathError
        for target in targets:
            if target.exists() and (
                not target.is_file() or target.stat().st_nlink > 1
            ):
                raise UnsafeWorkspacePathError
    except UnsafeWorkspacePathError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise UnsafeWorkspacePathError from error


def stored_identifier(name):
    """The identifier a stored snapshot file name belongs to: the whole stem
    with a trailing ``--<YYYYMMDDTHHMMSSZ>`` version stamp removed."""
    if not name.endswith(SNAPSHOT_SUFFIX):
        return None
    stem = name[: -len(SNAPSHOT_SUFFIX)]
    return VERSION_STAMP_PATTERN.sub("", stem)


def has_alias_conflict(snapshots_directory, canonical_job_id):
    """Detect a same-identifier-different-case snapshot in the script itself,
    because the filesystem underneath may be case-insensitive."""
    try:
        names = os.listdir(snapshots_directory)
    except OSError:
        return False
    folded = canonical_job_id.casefold()
    for name in names:
        identifier = stored_identifier(name)
        if identifier is None:
            continue
        if identifier != canonical_job_id and identifier.casefold() == folded:
            return True
    return False


# -- tracker references (read only; writing the tracker is another script) --


def normalise_reference(value, workspace):
    """Reduce a stored path to its workspace-relative, case-folded form.

    ``./jobs/snapshots/x.md``, ``jobs\\snapshots\\x.md`` and the absolute path
    under the workspace all name the same snapshot; anything outside the
    workspace, or not a string, names nothing.
    """
    if not isinstance(value, str):
        return None
    text = value.strip().replace("\\", "/")
    if not text:
        return None
    text = posixpath.normpath(text)
    if posixpath.isabs(text):
        root = posixpath.normpath(str(workspace))
        if not text.startswith(root + "/"):
            return None
        text = text[len(root) + 1 :]
    return text.casefold()


class SnapshotReference:
    """The one snapshot path a tracker scan is looking for."""

    def __init__(self, workspace, relative_path):
        self.workspace = workspace
        self.target = normalise_reference(relative_path, workspace)

    def matches(self, value):
        return self.target is not None and (
            normalise_reference(value, self.workspace) == self.target
        )

    def mentioned_anywhere(self, document):
        """Any string anywhere in a decoded event may carry the path, so the
        whole document is walked rather than one agreed key."""
        if isinstance(document, str):
            return self.matches(document)
        if isinstance(document, dict):
            return any(self.mentioned_anywhere(item) for item in document.values())
        if isinstance(document, (list, tuple)):
            return any(self.mentioned_anywhere(item) for item in document)
        return False


def open_tracker(tracker_path):
    """Open a tracker for streaming, or return None when it does not exist.

    A tracker larger than MAXIMUM_TRACKER_BYTES is refused rather than read: an
    unbounded read on attacker-influenced data can be killed for memory, and a
    killed process prints no report at all.
    """
    try:
        status = os.lstat(tracker_path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise TrackerUnreadableError from error
    if not stat.S_ISREG(status.st_mode) or status.st_size > MAXIMUM_TRACKER_BYTES:
        raise TrackerUnreadableError
    try:
        return open(tracker_path, "r", encoding="utf-8", newline="")
    except OSError as error:
        raise TrackerUnreadableError from error


def scan_tracker(tracker_path, mentions):
    """Fail closed: a tracker that cannot be read counts as a reference."""
    try:
        handle = open_tracker(tracker_path)
    except TrackerUnreadableError:
        return True
    if handle is None:
        return False
    try:
        with handle:
            return mentions(handle)
    except (OSError, ValueError, csv.Error, RecursionError):
        return True


def applications_csv_mentions(handle, reference):
    return any(
        reference.matches(row.get(SNAPSHOT_PATH_COLUMN))
        for row in csv.DictReader(handle)
    )


def events_jsonl_mentions(handle, reference):
    for line in handle:
        if not line.strip():
            continue
        if reference.mentioned_anywhere(json.loads(line)):
            return True
    return False


def is_referenced(workspace, relative_path):
    reference = SnapshotReference(workspace, relative_path)
    return scan_tracker(
        workspace / APPLICATIONS_CSV,
        lambda handle: applications_csv_mentions(handle, reference),
    ) or scan_tracker(
        workspace / EVENTS_JSONL,
        lambda handle: events_jsonl_mentions(handle, reference),
    )


# -- deciding, then writing -------------------------------------------------


def read_snapshot_bytes(snapshot_path):
    try:
        if not snapshot_path.is_file():
            return None
        return snapshot_path.read_bytes()
    except OSError as error:
        raise SnapshotUnreadableError from error


def plan_snapshot(workspace, canonical_job_id, instant, content_digest, rendered):
    """Choose the outcome without touching a single byte on disk."""
    base_relative = snapshot_relative_path(canonical_job_id)
    base_path = workspace / base_relative
    existing = read_snapshot_bytes(base_path)
    if existing is None:
        return CREATED, base_relative
    if existing == rendered:
        return UNCHANGED, base_relative
    if not is_referenced(workspace, base_relative):
        return UPDATED, base_relative
    if recorded_content_digest(base_path) == content_digest:
        return UNCHANGED, base_relative

    version_relative = snapshot_relative_path(canonical_job_id, instant)
    existing_version = read_snapshot_bytes(workspace / version_relative)
    if existing_version is None:
        return VERSIONED, version_relative
    if existing_version == rendered:
        return UNCHANGED, version_relative
    raise SnapshotCollisionError


def decide_snapshot(workspace, canonical_job_id, instant, content_digest, rendered):
    """Validate every path a write could touch, refuse an aliased identifier,
    then plan.  Grouping the read-only phases here is what makes
    validate-then-mutate structural rather than a matter of statement order.
    """
    validate_snapshot_paths(
        workspace,
        [
            snapshot_relative_path(canonical_job_id),
            snapshot_relative_path(canonical_job_id, instant),
        ],
    )
    if has_alias_conflict(workspace / SNAPSHOT_DIRECTORY, canonical_job_id):
        raise AliasConflictError
    return plan_snapshot(
        workspace, canonical_job_id, instant, content_digest, rendered
    )


def publish_temporary(directory_fd, temporary_name, name, exclusive):
    """Put the completed temporary file under its final name.

    A versioned name is created exclusively: an archived version is history,
    so a name that appeared since planning must be reported rather than won.
    """
    if not exclusive:
        os.replace(
            temporary_name, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd
        )
        return
    try:
        os.link(
            temporary_name, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd
        )
    except FileExistsError as error:
        raise SnapshotCollisionError from error
    os.unlink(temporary_name, dir_fd=directory_fd)


def write_snapshot(directory_fd, name, rendered, exclusive):
    """Write through a temporary file inside the already-opened directory.

    Every step is relative to ``directory_fd``, so the bytes land in the inode
    validation approved even if the directory is swapped underneath us.  The
    mode is set deliberately: a snapshot holds candidate-facing data and is
    private, not the 0644 the rest of the workspace uses.  The containing
    directory is fsynced too, because this is a history store and the rename
    has to survive a crash.
    """
    temporary_name = TEMPORARY_PREFIX + os.urandom(8).hex() + TEMPORARY_SUFFIX
    descriptor = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        SNAPSHOT_FILE_MODE,
        dir_fd=directory_fd,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), SNAPSHOT_FILE_MODE)
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        publish_temporary(directory_fd, temporary_name, name, exclusive)
        os.fsync(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except OSError:
            pass
        raise


def commit_snapshot(workspace, relative_path, rendered, exclusive):
    """Pin the validated snapshots directory by descriptor, then write."""
    directory = workspace / SNAPSHOT_DIRECTORY
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        pass
    try:
        directory_fd = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
    except OSError as error:
        raise UnsafeWorkspacePathError from error
    try:
        write_snapshot(
            directory_fd, posixpath.basename(relative_path), rendered, exclusive
        )
    finally:
        os.close(directory_fd)


# -- orchestration ----------------------------------------------------------


def load_validated_record(job_path, report):
    try:
        record = load_job_record(job_path)
    except JobRecordUnreadableError:
        report.error(
            "job_record_unreadable", "job", "The job record file could not be read."
        )
        return None
    except JobRecordMalformedError:
        report.error(
            "job_record_malformed", "job", "The job record file is not valid JSON."
        )
        return None
    if not isinstance(record, dict):
        report.error(
            "job_record_not_a_mapping",
            "job",
            "The job record must be a JSON object at its top level.",
        )
        return None
    return record


# Every way the path phases can fail, in the order they are tried, each with
# the diagnostic a caller sees.  Keeping them in one table is what lets
# build_report read as validate -> plan -> write.
SNAPSHOT_DIAGNOSTICS = (
    (
        AliasConflictError,
        "canonical_job_id_alias_conflict",
        IDENTITY_FIELD,
        "A stored snapshot already uses this identifier with different letter case.",
    ),
    (
        SnapshotCollisionError,
        "snapshot_version_collision",
        SNAPSHOT_PATH_COLUMN,
        "A different snapshot already occupies this version filename.",
    ),
    (
        SnapshotUnreadableError,
        "snapshot_unreadable",
        SNAPSHOT_PATH_COLUMN,
        "An existing snapshot could not be read.",
    ),
    (
        UnsafeWorkspacePathError,
        "snapshot_path_unsafe",
        SNAPSHOT_PATH_COLUMN,
        "The snapshot path is not a regular file contained in the workspace.",
    ),
    (
        OSError,
        "snapshot_write_failed",
        SNAPSHOT_PATH_COLUMN,
        "The snapshot could not be written into the workspace.",
    ),
)

SNAPSHOT_FAULTS = tuple(entry[0] for entry in SNAPSHOT_DIAGNOSTICS)


def diagnose(fault):
    for fault_type, code, field, message in SNAPSHOT_DIAGNOSTICS:
        if isinstance(fault, fault_type):
            return code, field, message
    raise fault


def build_report(workspace_argument, job_argument):
    """Validate, plan, write: at most one file is created or replaced."""
    report = SnapshotReport()
    try:
        workspace = resolve_workspace(workspace_argument)
    except UnsafeWorkspacePathError:
        report.error(
            "workspace_unsafe",
            "workspace",
            "The workspace must be an existing private directory outside this skill.",
        )
        return report

    record = load_validated_record(job_argument, report)
    if record is None:
        return report
    validate_fields(record, report)
    instants = validate_instants(record, report)
    if report.errors:
        return report

    content_digest = compute_content_digest(record)
    rendered = render_snapshot(record, content_digest)
    # The record has validated, so any rejection from here on can name it.
    report.canonical_job_id = record[IDENTITY_FIELD]
    report.content_digest = content_digest

    try:
        status, relative_path = decide_snapshot(
            workspace,
            record[IDENTITY_FIELD],
            instants["retrieved_at"],
            content_digest,
            rendered,
        )
        if status != UNCHANGED:
            commit_snapshot(
                workspace, relative_path, rendered, exclusive=status == VERSIONED
            )
    except SNAPSHOT_FAULTS as fault:
        report.error(*diagnose(fault))
        return report

    report.status = status
    report.snapshot_path = relative_path
    return report


def failure_report():
    report = SnapshotReport()
    report.error("snapshot_failed", "job", "The job snapshot could not be saved.")
    return report


def emit(report):
    payload = report.as_dict()
    print(json.dumps(payload))
    return 1 if payload["errors"] else 0


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Save an immutable job description snapshot into a workspace."
    )
    parser.add_argument(
        "--workspace",
        required=True,
        type=pathlib.Path,
        help="Path to the private workspace that owns the snapshot.",
    )
    parser.add_argument(
        "--job",
        required=True,
        type=pathlib.Path,
        help="Path to the JSON job record to snapshot.",
    )
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    try:
        exit_code = emit(build_report(arguments.workspace, arguments.job))
    except Exception:
        exit_code = emit(failure_report())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
