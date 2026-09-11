import argparse
import copy
import datetime
import json
import math
import pathlib
import stat
import sys

import yaml


UNKNOWN = "unknown"
DECLINED = "declined"
SENTINELS = (UNKNOWN, DECLINED)

REQUIRED_MARKET = "Australia"
LOCATION_MODES = ("onshore", "offshore")
EMPLOYMENT_STREAMS = ("professional", "casual", "part_time")
HOURLY_STREAMS = ("casual", "part_time")
WORK_RIGHTS_FIELDS = ("status", "restrictions", "sponsorship_required")
WORK_RIGHTS_STATUSES = ("unrestricted", "restricted", "none", UNKNOWN, DECLINED)
SALARY_BASES = ("annual", "hourly")
EVIDENCE_FIELDS = ("id", "claim", "source", "obtained_at")
EVIDENCE_PROVENANCE_CLASSES = (
    "candidate_statement",
    "candidate_document",
    "third_party_document",
    "verified_third_party",
)

MAXIMUM_PROFILE_BYTES = 1024 * 1024
ISO_DATE_LENGTH = len("YYYY-MM-DD")

QUESTIONS = {
    "target_roles": "Which role titles should this search target?",
    "target_locations": "Which Australian locations should this search cover?",
    "salary.target_aud": "What target pay in AUD should this search use?",
    "evidence_inventory": "Which evidence items can support the candidate's claims?",
    "work_rights.status": (
        "What is the candidate's current Australian work rights status?"
    ),
    "work_rights.restrictions": (
        "What restrictions apply to the candidate's Australian work rights?"
    ),
    "work_rights.sponsorship_required": (
        "Does the candidate require visa sponsorship to work in Australia?"
    ),
}

ABSENT = object()


class ProfileUnreadableError(Exception):
    pass


class ProfileMalformedError(Exception):
    pass


class ProfileReport:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.questions = []

    def error(self, code, field, message):
        self.errors.append({"code": code, "field": field, "message": message})

    def warn(self, code, field, message):
        self.warnings.append({"code": code, "field": field, "message": message})

    def ask(self, field, observed):
        if any(question["field"] == field for question in self.questions):
            return
        self.questions.append(
            {
                "field": field,
                "question": QUESTIONS[field],
                "observed": copy.deepcopy(observed),
            }
        )

    def as_dict(self):
        return {
            "valid": not self.errors,
            "errors": self.errors,
            "warnings": self.warnings,
            "next_questions": self.questions,
        }


def single_error_report(code, field, message):
    report = ProfileReport()
    report.error(code, field, message)
    return report


# -- small predicates ------------------------------------------------------


def read_field(mapping, key):
    return mapping.get(key, ABSENT)


def is_unrecorded(value):
    return value is ABSENT or value is None


def one_of(field, values):
    return f"{field} must be one of: " + ", ".join(values) + "."


def is_blank_text(value):
    return not isinstance(value, str) or not value.strip()


def is_positive_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return math.isfinite(value) and value > 0


def is_calendar_date(value):
    if isinstance(value, datetime.datetime):
        return False
    if isinstance(value, datetime.date):
        return True
    if not isinstance(value, str) or len(value) != ISO_DATE_LENGTH:
        return False
    if value[4] != "-" or value[7] != "-":
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def sentinel_of(value):
    if not isinstance(value, str):
        return None
    folded = value.strip().casefold()
    return folded if folded in SENTINELS else None


def note_sentinel(report, field, value):
    sentinel = sentinel_of(value)
    if sentinel == UNKNOWN:
        report.warn(
            "field_unknown",
            field,
            f"{field} is recorded as unknown and still needs a candidate answer.",
        )
        report.ask(field, value)
        return True
    if sentinel == DECLINED:
        report.warn(
            "sensitive_field_declined",
            field,
            f"{field} was declined by the candidate and is not re-asked.",
        )
        return True
    return False


# -- list-shaped fields ----------------------------------------------------

MISSING = "missing"
NOT_A_LIST = "not_a_list"
EMPTY = "empty"
ENTRIES = "entries"

EMPLOYMENT_STREAM_MESSAGES = {
    MISSING: "The profile must record at least one employment stream.",
    NOT_A_LIST: "{key} must be a list; a single value is not read as a list.",
    EMPTY: "{key} must name at least one stream.",
}

TARGET_LIST_MESSAGES = {
    MISSING: "{key} is not recorded yet.",
    NOT_A_LIST: "{key} must be a list; a single value is not read as a list.",
    EMPTY: "{key} is empty and still needs candidate input.",
}

EVIDENCE_INVENTORY_MESSAGES = {
    MISSING: "The profile must record an evidence inventory, even an empty one.",
    NOT_A_LIST: "{key} must be a list of evidence entries.",
    EMPTY: "{key} is empty, so no claim is supported yet.",
}


def list_state(value):
    if is_unrecorded(value):
        return MISSING
    if not isinstance(value, list):
        return NOT_A_LIST
    if not value:
        return EMPTY
    return ENTRIES


def note_list_state(report, key, state, messages, ask_states=(), observed=None):
    code = f"{key}_{state}"
    message = messages[state].format(key=key)
    if state in ask_states:
        report.warn(code, key, message)
        report.ask(key, observed)
    else:
        report.error(code, key, message)


# -- per-field rules -------------------------------------------------------


def check_market(report, document, key):
    value = read_field(document, key)
    if is_unrecorded(value):
        report.error("market_missing", key, "The profile must record a market.")
    elif value != REQUIRED_MARKET:
        report.error(
            "market_not_australia", key, f"{key} must be exactly {REQUIRED_MARKET!r}."
        )


def check_location_mode(report, document, key):
    value = read_field(document, key)
    if is_unrecorded(value):
        report.error(
            "location_mode_missing",
            key,
            "The profile must record whether the candidate is onshore or offshore.",
        )
    elif value not in LOCATION_MODES:
        report.error("location_mode_invalid", key, one_of(key, LOCATION_MODES))


def check_employment_streams(report, document, key):
    value = read_field(document, key)
    state = list_state(value)
    if state != ENTRIES:
        note_list_state(report, key, state, EMPLOYMENT_STREAM_MESSAGES)
        return
    for index, stream in enumerate(value):
        if stream not in EMPLOYMENT_STREAMS:
            report.error(
                f"{key}_invalid_value",
                f"{key}[{index}]",
                one_of(f"{key} entries", EMPLOYMENT_STREAMS),
            )


def check_target_list(report, document, key):
    value = read_field(document, key)
    state = list_state(value)
    if state != ENTRIES:
        note_list_state(
            report,
            key,
            state,
            TARGET_LIST_MESSAGES,
            ask_states=(MISSING, EMPTY),
            observed=value if state == EMPTY else None,
        )
        return
    for index, entry in enumerate(value):
        if is_blank_text(entry):
            report.error(
                f"{key}_invalid_value",
                f"{key}[{index}]",
                f"{key} entries must be non-empty text.",
            )


def check_work_rights(report, document, key):
    value = read_field(document, key)
    if is_unrecorded(value):
        report.error(
            "work_rights_missing",
            key,
            "The profile must record Australian work rights.",
        )
        return
    if not isinstance(value, dict):
        report.error(
            "work_rights_not_a_mapping",
            key,
            f"{key} must be a mapping of " + ", ".join(WORK_RIGHTS_FIELDS) + ".",
        )
        return
    for name in WORK_RIGHTS_FIELDS:
        field = f"{key}.{name}"
        entry = read_field(value, name)
        if entry is ABSENT:
            report.error(
                "work_rights_field_missing", field, f"{field} is not recorded."
            )
        elif not note_sentinel(report, field, entry):
            check_work_rights_value(report, field, name, entry)


def check_work_rights_value(report, field, name, value):
    if name == "status" and value not in WORK_RIGHTS_STATUSES:
        report.error(
            "work_rights_status_invalid", field, one_of(field, WORK_RIGHTS_STATUSES)
        )
    elif name == "restrictions" and is_blank_text(value):
        report.error(
            "work_rights_restrictions_invalid",
            field,
            f"{field} must be text describing the restrictions.",
        )
    elif name == "sponsorship_required" and not isinstance(value, bool):
        report.error(
            "work_rights_sponsorship_required_invalid",
            field,
            f"{field} must be true, false, or a preserved sentinel.",
        )


def check_salary(report, document, key):
    value = read_field(document, key)
    if is_unrecorded(value):
        report.error("salary_missing", key, "The profile must record a salary basis.")
        return
    if not isinstance(value, dict):
        report.error(
            "salary_not_a_mapping",
            key,
            f"{key} must be a mapping of basis and target_aud.",
        )
        return
    check_salary_basis(report, f"{key}.basis", read_field(value, "basis"))
    check_salary_target(report, f"{key}.target_aud", read_field(value, "target_aud"))


def check_salary_basis(report, field, basis):
    if basis is ABSENT:
        report.error("salary_basis_missing", field, f"{field} is not recorded.")
    elif basis not in SALARY_BASES:
        report.error("salary_basis_invalid", field, one_of(field, SALARY_BASES))


def check_salary_target(report, field, target):
    if is_unrecorded(target):
        if target is ABSENT:
            code, detail = "salary_target_missing", "is not recorded yet"
        else:
            code, detail = "salary_target_unknown", "is recorded as unknown"
        report.warn(
            code, field, f"{field} {detail} and still needs candidate input."
        )
        report.ask(field, None)
        return
    if note_sentinel(report, field, target):
        return
    if not is_positive_number(target):
        report.error(
            "salary_target_invalid",
            field,
            f"{field} must be a finite positive number, null, or a preserved sentinel.",
        )


def check_evidence_inventory(report, document, key):
    value = read_field(document, key)
    state = list_state(value)
    if state != ENTRIES:
        note_list_state(
            report,
            key,
            state,
            EVIDENCE_INVENTORY_MESSAGES,
            ask_states=(EMPTY,),
            observed=value,
        )
        return
    seen_ids = set()
    for index, entry in enumerate(value):
        check_evidence_entry(report, f"{key}[{index}]", entry, seen_ids)


def check_evidence_entry(report, prefix, entry, seen_ids):
    if not isinstance(entry, dict):
        report.error(
            "evidence_entry_not_a_mapping",
            prefix,
            "each evidence entry must be a mapping with "
            + ", ".join(EVIDENCE_FIELDS)
            + ".",
        )
        return
    for name in EVIDENCE_FIELDS:
        field = f"{prefix}.{name}"
        value = read_field(entry, name)
        if value is ABSENT:
            report.error(
                "evidence_provenance_missing",
                field,
                f"{name} is required on every evidence entry.",
            )
        elif not is_valid_evidence_value(name, value):
            report.error(
                "evidence_provenance_malformed", field, evidence_requirement(name)
            )
        elif name == "id":
            if value in seen_ids:
                report.error(
                    "evidence_id_duplicated",
                    field,
                    "evidence ids must be unique so every claim stays traceable.",
                )
            seen_ids.add(value)


def is_valid_evidence_value(name, value):
    if name in ("id", "claim"):
        return not is_blank_text(value)
    if name == "source":
        return value in EVIDENCE_PROVENANCE_CLASSES
    return is_calendar_date(value)


def evidence_requirement(name):
    if name in ("id", "claim"):
        return f"{name} must be non-empty text."
    if name == "source":
        return one_of("source", EVIDENCE_PROVENANCE_CLASSES)
    return "obtained_at must be a calendar date in YYYY-MM-DD form."


# -- cross-field rules -----------------------------------------------------


def check_salary_stream_alignment(report, document):
    streams = read_field(document, "employment_streams")
    salary = read_field(document, "salary")
    if not isinstance(streams, list) or not isinstance(salary, dict):
        return
    hourly_only = any(stream in HOURLY_STREAMS for stream in streams) and (
        "professional" not in streams
    )
    if hourly_only and salary.get("basis") == "annual":
        report.warn(
            "salary_basis_stream_mismatch",
            "salary.basis",
            "casual or part-time streams are usually paid hourly; the recorded "
            "annual basis is kept as written and needs candidate confirmation.",
        )


def check_unrecognised_fields(report, document):
    for key in document:
        if key in KNOWN_TOP_LEVEL_KEYS:
            continue
        field = key if isinstance(key, str) else repr(key)
        report.warn(
            "unrecognised_field",
            field,
            f"{field} is not part of the candidate profile contract; it is kept as "
            "written and not interpreted.",
        )


PROFILE_RULES = (
    ("market", check_market),
    ("candidate_location_mode", check_location_mode),
    ("employment_streams", check_employment_streams),
    ("target_roles", check_target_list),
    ("target_locations", check_target_list),
    ("work_rights", check_work_rights),
    ("salary", check_salary),
    ("evidence_inventory", check_evidence_inventory),
)

KNOWN_TOP_LEVEL_KEYS = frozenset(key for key, _rule in PROFILE_RULES)

CROSS_FIELD_CHECKS = (check_salary_stream_alignment, check_unrecognised_fields)


def validate_profile(document):
    document = copy.deepcopy(document)
    report = ProfileReport()
    for key, rule in PROFILE_RULES:
        rule(report, document, key)
    for check in CROSS_FIELD_CHECKS:
        check(report, document)
    return report


# -- input handling --------------------------------------------------------


def read_profile_text(profile_path):
    try:
        status = profile_path.stat()
    except OSError as error:
        raise ProfileUnreadableError from error
    if not stat.S_ISREG(status.st_mode):
        raise ProfileUnreadableError
    if status.st_size > MAXIMUM_PROFILE_BYTES:
        raise ProfileUnreadableError
    try:
        return profile_path.read_text(encoding="utf-8")
    except (OSError, ValueError) as error:
        raise ProfileUnreadableError from error


def load_profile(profile_path):
    contents = read_profile_text(profile_path)
    try:
        return yaml.safe_load(contents)
    except (yaml.YAMLError, RecursionError) as error:
        raise ProfileMalformedError from error


def build_report(profile_path):
    try:
        document = load_profile(profile_path)
    except ProfileUnreadableError:
        return single_error_report(
            "profile_unreadable", "profile", "The profile file could not be read."
        )
    except ProfileMalformedError:
        return single_error_report(
            "profile_malformed", "profile", "The profile file is not valid YAML."
        )
    if not isinstance(document, dict):
        return single_error_report(
            "profile_not_a_mapping",
            "profile",
            "The profile file must contain a YAML mapping at its top level.",
        )
    return validate_profile(document)


def emit(report):
    payload = report.as_dict()
    rendered = json.dumps(payload)
    print(rendered)
    return 1 if payload["errors"] else 0


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Validate a candidate profile without changing it."
    )
    parser.add_argument(
        "--profile",
        required=True,
        type=pathlib.Path,
        help="Path to the candidate profile YAML file to read.",
    )
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    try:
        exit_code = emit(build_report(arguments.profile))
    except Exception:
        exit_code = emit(
            single_error_report(
                "validator_failed", "profile", "The profile could not be validated."
            )
        )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
