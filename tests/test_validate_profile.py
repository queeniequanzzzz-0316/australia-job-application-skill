import contextlib
import copy
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_profile.py"
SCRIPTS_DIRECTORY = ROOT / "scripts"

REPORT_KEYS = {"valid", "errors", "warnings", "next_questions"}
DIAGNOSTIC_KEYS = {"code", "field", "message"}
QUESTION_KEYS = {"field", "question", "observed"}

EVIDENCE_PROVENANCE_CLASSES = (
    "candidate_statement",
    "candidate_document",
    "third_party_document",
    "verified_third_party",
)

REJECTED_EVIDENCE_SOURCES = (
    "resume",
    "cover_letter",
    "certificate",
    "transcript",
    "reference",
    "employer_record",
    "portfolio",
    "document",
    "hearsay",
    "linkedin",
    "assumed",
    "unknown",
)

LOCATION_MODES = ("onshore", "offshore")
EMPLOYMENT_STREAMS = ("professional", "casual", "part_time")
REJECTED_CLOSED_SET_VALUES = ("hybrid", "remote", "gig", "unknown", "declined", "", 7)

SUBPROCESS_TIMEOUT_SECONDS = 60
NON_REGULAR_TIMEOUT_SECONDS = 10
OVERSIZE_PROFILE_BYTES = 64 * 1024 * 1024
NESTING_DEPTH = 80000

MISSING = object()

VALID_PROFILE = {
    "market": "Australia",
    "candidate_location_mode": "onshore",
    "employment_streams": ["professional"],
    "target_roles": ["Software Engineer"],
    "target_locations": ["Melbourne VIC"],
    "work_rights": {
        "status": "unrestricted",
        "restrictions": "none",
        "sponsorship_required": False,
    },
    "salary": {"basis": "annual", "target_aud": 150000},
    "evidence_inventory": [
        {
            "id": "ev-1",
            "claim": "Five years of backend engineering",
            "source": "candidate_document",
            "obtained_at": "2026-09-01",
        }
    ],
}


def profile_with(**overrides):
    profile = copy.deepcopy(VALID_PROFILE)
    profile.update(copy.deepcopy(overrides))
    return profile


def profile_without(*keys):
    profile = copy.deepcopy(VALID_PROFILE)
    for key in keys:
        profile.pop(key, None)
    return profile


def evidence_entry(**overrides):
    entry = {
        "id": "ev-1",
        "claim": "Led a migration",
        "source": "candidate_document",
        "obtained_at": "2026-09-01",
    }
    entry.update(copy.deepcopy(overrides))
    return entry


def render_yaml(profile):
    return yaml.safe_dump(profile, sort_keys=False, allow_unicode=True)


def profile_raw_with_obtained_at(obtained_at_text):
    return render_yaml(profile_without("evidence_inventory")) + (
        "evidence_inventory:\n"
        "  - id: ev-1\n"
        "    claim: Led a migration\n"
        "    source: candidate_document\n"
        f"    obtained_at: {obtained_at_text}\n"
    )


def profile_raw_with_salary_target(target_text):
    return render_yaml(profile_without("salary")) + (
        "salary:\n  basis: annual\n" f"  target_aud: {target_text}\n"
    )


def profile_raw_with_sponsorship_required(sponsorship_text):
    return render_yaml(profile_without("work_rights")) + (
        "work_rights:\n"
        "  status: unrestricted\n"
        "  restrictions: none\n"
        f"  sponsorship_required: {sponsorship_text}\n"
    )


def source_value(document, field):
    node = document
    for part in field.replace("[", ".").replace("]", "").split("."):
        if not part:
            continue
        if isinstance(node, dict):
            if part not in node:
                return MISSING
            node = node[part]
        elif isinstance(node, list):
            index = int(part)
            if index >= len(node):
                return MISSING
            node = node[index]
        else:
            return MISSING
    return node


class ValidateProfileTestCase(unittest.TestCase):
    maxDiff = None

    def run_validator(self, *arguments, timeout=SUBPROCESS_TIMEOUT_SECONDS):
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
                f"validator did not finish within {timeout}s for arguments "
                f"{tuple(map(str, arguments))!r}; it must always terminate with a "
                "JSON report"
            )

    @contextlib.contextmanager
    def profile_file(self, profile=None, raw=None, name="candidate-profile.yaml"):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = pathlib.Path(temporary_directory)
            profile_path = directory / name
            if raw is None:
                raw = render_yaml(profile)
            profile_path.write_text(raw, encoding="utf-8")
            yield profile_path

    def parse_report(self, result):
        stdout = result.stdout
        try:
            report = json.loads(stdout)
        except ValueError:
            self.fail(
                "validator must print exactly one JSON object on stdout; "
                f"got returncode={result.returncode} stdout={stdout!r} "
                f"stderr={result.stderr!r}"
            )
        self.assertIsInstance(report, dict, f"report must be a JSON object: {stdout!r}")
        self.assertEqual(set(report), REPORT_KEYS, "unexpected top-level report keys")
        self.assertIsInstance(report["valid"], bool)
        for bucket in ("errors", "warnings"):
            self.assertIsInstance(report[bucket], list)
            for entry in report[bucket]:
                self.assertIsInstance(entry, dict)
                self.assertEqual(set(entry), DIAGNOSTIC_KEYS, f"bad {bucket} entry")
        self.assertIsInstance(report["next_questions"], list)
        for entry in report["next_questions"]:
            self.assertIsInstance(entry, dict)
            self.assertEqual(set(entry), QUESTION_KEYS, "bad next_questions entry")
        self.assertEqual(report["valid"], report["errors"] == [])
        self.assertEqual(
            result.returncode,
            0 if report["errors"] == [] else 1,
            f"exit code must be 0 iff errors is empty; stderr={result.stderr!r}",
        )
        self.assertNotIn("traceback", result.stderr.lower())
        return report

    def assert_paths_redacted(self, report, profile_path):
        secrets = {
            str(profile_path),
            str(profile_path.parent),
            str(profile_path.resolve()),
            profile_path.name,
        }
        texts = [entry["message"] for entry in report["errors"] + report["warnings"]]
        texts += [entry["question"] for entry in report["next_questions"]]
        for text in texts:
            for secret in secrets:
                self.assertNotIn(secret, text, "report leaked the profile path")

    def validate_path(self, profile_path, timeout=SUBPROCESS_TIMEOUT_SECONDS):
        result = self.run_validator("--profile", profile_path, timeout=timeout)
        report = self.parse_report(result)
        self.assert_paths_redacted(report, profile_path)
        return result, report

    def validate(self, profile=None, raw=None):
        with self.profile_file(profile=profile, raw=raw) as profile_path:
            return self.validate_path(profile_path)

    def codes(self, entries):
        return [entry["code"] for entry in entries]

    def fields_for(self, entries, code):
        return [entry["field"] for entry in entries if entry["code"] == code]

    def question_for(self, report, field):
        matches = [
            entry for entry in report["next_questions"] if entry["field"] == field
        ]
        self.assertEqual(len(matches), 1, f"expected one next_question for {field}")
        return matches[0]

    def assert_no_question_for(self, report, field):
        self.assertNotIn(
            field,
            [entry["field"] for entry in report["next_questions"]],
            f"{field} must not be re-asked",
        )

    def assert_report_contains_no_inference(self, report, document):
        for entry in report["next_questions"]:
            field = entry["field"]
            observed = entry["observed"]
            recorded = source_value(document, field)
            if recorded is MISSING:
                self.assertIsNone(
                    observed,
                    f"{field} is absent from the source document, so observed must "
                    f"be null; got {observed!r}",
                )
                field_codes = [
                    warning["code"]
                    for warning in report["warnings"]
                    if warning["field"] == field
                ]
                self.assertTrue(
                    field_codes,
                    f"{field} is absent from the source document and must be warned "
                    "about before it is re-asked",
                )
                self.assertTrue(
                    any(code.endswith("_missing") for code in field_codes),
                    f"{field} is absent from the source document, so its warning code "
                    f"must end in _missing; got {field_codes}",
                )
                continue
            self.assertEqual(
                observed,
                recorded,
                f"observed for {field} must echo the source document verbatim; "
                f"source={recorded!r} observed={observed!r}",
            )
            self.assertIs(
                type(observed),
                type(recorded),
                f"observed for {field} changed type; source={recorded!r} "
                f"observed={observed!r}",
            )


class ValidateProfileTests(ValidateProfileTestCase):
    # -- Scenario 1: complete valid professional profile --------------------
    def test_scenario_01_complete_professional_profile_is_valid(self):
        result, report = self.validate(VALID_PROFILE)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(report["valid"], True)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["next_questions"], [])

    # -- Scenario 2: complete valid casual / part-time profile --------------
    def test_scenario_02_complete_casual_part_time_profile_is_valid(self):
        profile = profile_with(
            employment_streams=["casual", "part_time"],
            salary={"basis": "hourly", "target_aud": 45},
        )

        result, report = self.validate(profile)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(report["valid"], True)
        self.assertEqual(report["errors"], [])
        self.assertNotIn("salary_basis_stream_mismatch", self.codes(report["warnings"]))

    # -- Scenario 3: onshore candidate accepted -----------------------------
    def test_scenario_03_onshore_candidate_with_unknown_work_rights_is_still_asked(self):
        profile = profile_with(
            candidate_location_mode="onshore",
            work_rights={
                "status": "unknown",
                "restrictions": "unknown",
                "sponsorship_required": "unknown",
            },
        )

        result, report = self.validate(profile)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(report["valid"], True)
        self.assertEqual(report["errors"], [])
        unknown_fields = self.fields_for(report["warnings"], "field_unknown")
        for field in ("work_rights.status", "work_rights.sponsorship_required"):
            with self.subTest(field=field):
                self.assertIn(field, unknown_fields)
                self.assertEqual(self.question_for(report, field)["observed"], "unknown")
        blob = json.dumps(report)
        for inference in ('"sponsorship_required": false', '"observed": false'):
            self.assertNotIn(
                inference,
                blob,
                "an onshore candidate must not have sponsorship_required resolved for "
                "them",
            )
        self.assert_report_contains_no_inference(report, profile)

    # -- Scenario 4: offshore with sponsorship required ---------------------
    def test_scenario_04_offshore_sponsorship_required_is_accepted_without_inference(self):
        profile = profile_with(
            candidate_location_mode="offshore",
            work_rights={
                "status": "none",
                "restrictions": "no current Australian work rights",
                "sponsorship_required": True,
            },
        )

        result, report = self.validate(profile)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(report["valid"], True)
        self.assertEqual(report["errors"], [])
        self.assert_no_question_for(report, "work_rights.sponsorship_required")
        self.assert_report_contains_no_inference(report, profile)

    # -- Scenario 5: missing optional evidence ------------------------------
    def test_scenario_05_empty_evidence_inventory_warns_and_asks_but_stays_valid(self):
        profile = profile_with(evidence_inventory=[])

        result, report = self.validate(profile)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(report["valid"], True)
        self.assertEqual(report["errors"], [])
        self.assertIn("evidence_inventory_empty", self.codes(report["warnings"]))
        self.assertEqual(
            self.fields_for(report["warnings"], "evidence_inventory_empty"),
            ["evidence_inventory"],
        )
        question = self.question_for(report, "evidence_inventory")
        self.assertEqual(question["observed"], [])

    # -- Scenario 6: missing required structural fields ---------------------
    def test_scenario_06a_missing_market_is_an_error(self):
        result, report = self.validate(profile_without("market"))

        self.assertEqual(result.returncode, 1)
        self.assertIs(report["valid"], False)
        self.assertIn("market_missing", self.codes(report["errors"]))
        self.assertEqual(self.fields_for(report["errors"], "market_missing"), ["market"])

    def test_scenario_06b_missing_employment_streams_is_an_error(self):
        result, report = self.validate(profile_without("employment_streams"))

        self.assertEqual(result.returncode, 1)
        self.assertIs(report["valid"], False)
        self.assertIn("employment_streams_missing", self.codes(report["errors"]))

    def test_scenario_06c_missing_location_mode_is_an_error(self):
        result, report = self.validate(profile_without("candidate_location_mode"))

        self.assertEqual(result.returncode, 1)
        self.assertIn("location_mode_missing", self.codes(report["errors"]))

    # -- Scenario 7: unknown sentinel ---------------------------------------
    def test_scenario_07_unknown_sentinel_warns_and_asks_and_is_not_an_error(self):
        profile = profile_with(
            work_rights={
                "status": "unknown",
                "restrictions": "unknown",
                "sponsorship_required": "unknown",
            }
        )

        result, report = self.validate(profile)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(report["valid"], True)
        self.assertEqual(report["errors"], [])
        for field in (
            "work_rights.status",
            "work_rights.restrictions",
            "work_rights.sponsorship_required",
        ):
            with self.subTest(field=field):
                self.assertIn(
                    field, self.fields_for(report["warnings"], "field_unknown")
                )
                self.assertEqual(self.question_for(report, field)["observed"], "unknown")
        self.assert_report_contains_no_inference(report, profile)

    # -- Scenario 8: declined sensitive field -------------------------------
    def test_scenario_08_declined_sentinel_warns_without_a_question_and_is_preserved(self):
        profile = profile_with(
            work_rights={
                "status": "declined",
                "restrictions": "declined",
                "sponsorship_required": "declined",
            }
        )

        with self.profile_file(profile) as profile_path:
            original = profile_path.read_bytes()
            result, report = self.validate_path(profile_path)
            self.assertEqual(profile_path.read_bytes(), original)
            reloaded = yaml.safe_load(profile_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(report["valid"], True)
        self.assertEqual(report["errors"], [])
        declined_fields = self.fields_for(report["warnings"], "sensitive_field_declined")
        for field in (
            "work_rights.status",
            "work_rights.restrictions",
            "work_rights.sponsorship_required",
        ):
            with self.subTest(field=field):
                self.assertIn(field, declined_fields)
                self.assert_no_question_for(report, field)
        self.assertEqual(
            reloaded["work_rights"],
            {
                "status": "declined",
                "restrictions": "declined",
                "sponsorship_required": "declined",
            },
        )

    # -- Scenario 9: invalid location mode ----------------------------------
    def test_scenario_09_invalid_location_mode_is_an_error(self):
        result, report = self.validate(profile_with(candidate_location_mode="remote"))

        self.assertEqual(result.returncode, 1)
        self.assertIs(report["valid"], False)
        self.assertIn("location_mode_invalid", self.codes(report["errors"]))
        self.assertEqual(
            self.fields_for(report["errors"], "location_mode_invalid"),
            ["candidate_location_mode"],
        )

    def test_scenario_09b_location_mode_unknown_is_invalid_not_a_sentinel(self):
        result, report = self.validate(profile_with(candidate_location_mode="unknown"))

        self.assertEqual(result.returncode, 1)
        self.assertIs(report["valid"], False)
        self.assertIn("location_mode_invalid", self.codes(report["errors"]))
        self.assertNotIn(
            "candidate_location_mode",
            self.fields_for(report["warnings"], "field_unknown"),
        )

    # -- Scenario 10: invalid employment streams ----------------------------
    def test_scenario_10a_invalid_employment_stream_value_is_an_error(self):
        result, report = self.validate(
            profile_with(employment_streams=["professional", "contract"])
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("employment_streams_invalid_value", self.codes(report["errors"]))

    def test_scenario_10b_scalar_employment_streams_is_never_coerced_to_a_list(self):
        profile = profile_with(employment_streams="professional")

        result, report = self.validate(profile)

        self.assertEqual(result.returncode, 1)
        self.assertIs(report["valid"], False)
        self.assertIn("employment_streams_not_a_list", self.codes(report["errors"]))
        self.assertNotIn("employment_streams_invalid_value", self.codes(report["errors"]))
        self.assert_report_contains_no_inference(report, profile)

    def test_scenario_10c_empty_employment_streams_is_an_error(self):
        result, report = self.validate(profile_with(employment_streams=[]))

        self.assertEqual(result.returncode, 1)
        self.assertIn("employment_streams_empty", self.codes(report["errors"]))

    # -- Scenario 11: evidence provenance -----------------------------------
    def test_scenario_11a_missing_evidence_provenance_is_an_error(self):
        entry = evidence_entry()
        entry.pop("obtained_at")

        result, report = self.validate(profile_with(evidence_inventory=[entry]))

        self.assertEqual(result.returncode, 1)
        self.assertIs(report["valid"], False)
        self.assertIn("evidence_provenance_missing", self.codes(report["errors"]))
        self.assertEqual(
            self.fields_for(report["errors"], "evidence_provenance_missing"),
            ["evidence_inventory[0].obtained_at"],
        )

    def test_scenario_11b_malformed_evidence_provenance_is_an_error(self):
        cases = {
            "source": evidence_entry(source="hearsay"),
            "obtained_at": evidence_entry(obtained_at="01/09/2026"),
            "id": evidence_entry(id=""),
            "claim": evidence_entry(claim="   "),
        }

        for key, entry in cases.items():
            with self.subTest(malformed=key):
                result, report = self.validate(
                    profile_with(evidence_inventory=[entry])
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    self.codes(report["errors"]), ["evidence_provenance_malformed"]
                )
                self.assertEqual(
                    self.fields_for(report["errors"], "evidence_provenance_malformed"),
                    [f"evidence_inventory[0].{key}"],
                )

    def test_scenario_11c_evidence_entry_must_be_a_mapping(self):
        result, report = self.validate(
            profile_with(evidence_inventory=["a bare claim string"])
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("evidence_entry_not_a_mapping", self.codes(report["errors"]))

    def test_scenario_11d_evidence_inventory_missing_or_not_a_list(self):
        _, missing_report = self.validate(profile_without("evidence_inventory"))
        self.assertIn("evidence_inventory_missing", self.codes(missing_report["errors"]))

        _, scalar_report = self.validate(profile_with(evidence_inventory="ev-1"))
        self.assertIn(
            "evidence_inventory_not_a_list", self.codes(scalar_report["errors"])
        )

    # -- Scenario 12: nothing is ever inferred or defaulted -----------------
    def test_scenario_12_report_never_inserts_an_inferred_or_default_value(self):
        profile = profile_with(
            candidate_location_mode="offshore",
            target_roles=[],
            target_locations=[],
            salary={"basis": "annual", "target_aud": None},
            work_rights={
                "status": "unknown",
                "restrictions": "unknown",
                "sponsorship_required": "unknown",
            },
            evidence_inventory=[],
        )

        result, report = self.validate(profile)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(report["valid"], True)
        self.assertEqual(report["errors"], [])
        self.assert_report_contains_no_inference(report, profile)
        self.assertEqual(self.question_for(report, "target_roles")["observed"], [])
        self.assertEqual(self.question_for(report, "target_locations")["observed"], [])
        self.assertIsNone(self.question_for(report, "salary.target_aud")["observed"])
        self.assertEqual(self.question_for(report, "evidence_inventory")["observed"], [])
        for field in (
            "work_rights.status",
            "work_rights.restrictions",
            "work_rights.sponsorship_required",
        ):
            self.assertEqual(self.question_for(report, field)["observed"], "unknown")
        warning_codes = set(self.codes(report["warnings"]))
        for expected in (
            "target_roles_empty",
            "target_locations_empty",
            "salary_target_unknown",
            "evidence_inventory_empty",
            "field_unknown",
        ):
            self.assertIn(expected, warning_codes)

    # -- Scenario 13: malformed input ---------------------------------------
    def test_scenario_13a_unparseable_yaml_reports_profile_malformed(self):
        result, report = self.validate(raw="market: Australia\nwork_rights: {status: x\n")

        self.assertEqual(result.returncode, 1)
        self.assertIs(report["valid"], False)
        self.assertEqual(self.codes(report["errors"]), ["profile_malformed"])
        self.assertNotIn("traceback", result.stderr.lower())

    def test_scenario_13b_non_mapping_root_reports_profile_not_a_mapping(self):
        for label, raw in (
            ("list", "- market: Australia\n- candidate_location_mode: onshore\n"),
            ("scalar", "just a bare string\n"),
        ):
            with self.subTest(root=label):
                result, report = self.validate(raw=raw)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.codes(report["errors"]), ["profile_not_a_mapping"])
                self.assertNotIn("traceback", result.stderr.lower())

    def test_scenario_13c_missing_profile_reports_profile_unreadable(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing = pathlib.Path(temporary_directory) / "no-such-profile.yaml"
            self.assertFalse(missing.exists())

            result, report = self.validate_path(missing)

        self.assertEqual(result.returncode, 1)
        self.assertIs(report["valid"], False)
        self.assertEqual(self.codes(report["errors"]), ["profile_unreadable"])
        self.assertNotIn("traceback", result.stderr.lower())

    def test_scenario_13d_unreadable_profile_reports_profile_unreadable(self):
        if os.geteuid() == 0:
            self.skipTest("root bypasses read permission bits")
        with self.profile_file(VALID_PROFILE) as profile_path:
            profile_path.chmod(0o000)
            try:
                with profile_path.open("rb"):
                    self.skipTest("filesystem does not enforce read denial")
            except PermissionError:
                pass
            try:
                result, report = self.validate_path(profile_path)
            finally:
                profile_path.chmod(0o600)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.codes(report["errors"]), ["profile_unreadable"])
        self.assertNotIn("traceback", result.stderr.lower())

    def test_scenario_13e_directory_as_profile_reports_profile_unreadable(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = pathlib.Path(temporary_directory) / "candidate-profile.yaml"
            directory.mkdir()

            result, report = self.validate_path(directory)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.codes(report["errors"]), ["profile_unreadable"])
        self.assertNotIn("traceback", result.stderr.lower())

    # -- Scenario 14: the source profile is read-only -----------------------
    def test_scenario_14_source_profile_is_never_mutated(self):
        profile = profile_with(
            target_roles=[],
            work_rights={
                "status": "unknown",
                "restrictions": "declined",
                "sponsorship_required": "unknown",
            },
        )

        with self.profile_file(profile) as profile_path:
            directory = profile_path.parent
            bytes_before = profile_path.read_bytes()
            stat_before = profile_path.stat()
            listing_before = sorted(path.name for path in directory.iterdir())

            result, report = self.validate_path(profile_path)

            self.assertEqual(profile_path.read_bytes(), bytes_before)
            stat_after = profile_path.stat()
            self.assertEqual(stat_after.st_mtime_ns, stat_before.st_mtime_ns)
            self.assertEqual(stat_after.st_size, stat_before.st_size)
            self.assertEqual(
                sorted(path.name for path in directory.iterdir()), listing_before
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(report["valid"], True)


class ValidateProfileContractTests(ValidateProfileTestCase):
    def test_market_must_be_exactly_australia(self):
        profile = profile_with(market="New Zealand")

        result, report = self.validate(profile)

        self.assertEqual(result.returncode, 1)
        self.assertIs(report["valid"], False)
        self.assertIn("market_not_australia", self.codes(report["errors"]))
        self.assertEqual(
            self.fields_for(report["errors"], "market_not_australia"), ["market"]
        )
        self.assert_report_contains_no_inference(report, profile)

    def test_work_rights_missing_and_not_a_mapping_and_incomplete(self):
        _, missing = self.validate(profile_without("work_rights"))
        self.assertIn("work_rights_missing", self.codes(missing["errors"]))

        _, scalar = self.validate(profile_with(work_rights="unrestricted"))
        self.assertIn("work_rights_not_a_mapping", self.codes(scalar["errors"]))

        _, partial = self.validate(
            profile_with(work_rights={"status": "unrestricted", "restrictions": "none"})
        )
        self.assertIn("work_rights_field_missing", self.codes(partial["errors"]))
        self.assertIn(
            "work_rights.sponsorship_required",
            self.fields_for(partial["errors"], "work_rights_field_missing"),
        )

    def test_work_rights_status_accepts_only_the_documented_values(self):
        for status in ("unrestricted", "restricted", "none", "unknown", "declined"):
            with self.subTest(status=status):
                result, report = self.validate(
                    profile_with(
                        work_rights={
                            "status": status,
                            "restrictions": "none",
                            "sponsorship_required": False,
                        }
                    )
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(
                    "work_rights_status_invalid", self.codes(report["errors"])
                )

        result, report = self.validate(
            profile_with(
                work_rights={
                    "status": "pending",
                    "restrictions": "none",
                    "sponsorship_required": False,
                }
            )
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("work_rights_status_invalid", self.codes(report["errors"]))
        self.assertEqual(
            self.fields_for(report["errors"], "work_rights_status_invalid"),
            ["work_rights.status"],
        )

    def test_salary_missing_or_invalid_basis_is_an_error(self):
        _, missing = self.validate(profile_without("salary"))
        self.assertIn("salary_missing", self.codes(missing["errors"]))

        _, bad_basis = self.validate(
            profile_with(salary={"basis": "monthly", "target_aud": 9000})
        )
        self.assertIn("salary_basis_invalid", self.codes(bad_basis["errors"]))
        self.assertEqual(
            self.fields_for(bad_basis["errors"], "salary_basis_invalid"),
            ["salary.basis"],
        )

    def test_salary_target_accepts_number_null_and_sentinels(self):
        for target in (150000, None, "unknown", "declined"):
            with self.subTest(target_aud=target):
                result, report = self.validate(
                    profile_with(salary={"basis": "annual", "target_aud": target})
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(report["errors"], [])

    def test_salary_target_unknown_sentinel_is_warned_and_re_asked_verbatim(self):
        result, report = self.validate(
            profile_with(salary={"basis": "annual", "target_aud": "unknown"})
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("field_unknown", self.codes(report["warnings"]))
        self.assertIn(
            "salary.target_aud", self.fields_for(report["warnings"], "field_unknown")
        )
        self.assertEqual(
            self.question_for(report, "salary.target_aud")["observed"], "unknown"
        )

    def test_salary_target_declined_is_never_re_asked(self):
        result, report = self.validate(
            profile_with(salary={"basis": "annual", "target_aud": "declined"})
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["errors"], [])
        self.assertIn(
            "salary.target_aud",
            self.fields_for(report["warnings"], "sensitive_field_declined"),
        )
        self.assert_no_question_for(report, "salary.target_aud")

    def test_salary_target_null_warns_and_asks_with_null_observed(self):
        result, report = self.validate(
            profile_with(salary={"basis": "annual", "target_aud": None})
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("salary_target_unknown", self.codes(report["warnings"]))
        self.assertIsNone(self.question_for(report, "salary.target_aud")["observed"])

    def test_target_roles_and_locations_must_be_lists_when_present(self):
        _, roles = self.validate(profile_with(target_roles="Software Engineer"))
        self.assertIn("target_roles_not_a_list", self.codes(roles["errors"]))

        _, locations = self.validate(profile_with(target_locations="Melbourne VIC"))
        self.assertIn("target_locations_not_a_list", self.codes(locations["errors"]))

    def test_empty_target_roles_and_locations_warn_and_ask_without_failing(self):
        result, report = self.validate(
            profile_with(target_roles=[], target_locations=[])
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(report["valid"], True)
        warning_codes = self.codes(report["warnings"])
        self.assertIn("target_roles_empty", warning_codes)
        self.assertIn("target_locations_empty", warning_codes)
        self.assertEqual(self.question_for(report, "target_roles")["observed"], [])
        self.assertEqual(self.question_for(report, "target_locations")["observed"], [])

    def test_offshore_unknown_sponsorship_is_asked_not_inferred_as_required(self):
        profile = profile_with(
            candidate_location_mode="offshore",
            work_rights={
                "status": "unknown",
                "restrictions": "unknown",
                "sponsorship_required": "unknown",
            },
        )

        result, report = self.validate(profile)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["errors"], [])
        self.assertIn(
            "work_rights.sponsorship_required",
            self.fields_for(report["warnings"], "field_unknown"),
        )
        question = self.question_for(report, "work_rights.sponsorship_required")
        self.assertEqual(question["observed"], "unknown")
        self.assert_report_contains_no_inference(report, profile)
        blob = json.dumps(report)
        for inference in ("sponsorship_required: true", "\"sponsorship_required\": true"):
            self.assertNotIn(inference, blob)

    def test_casual_stream_with_annual_basis_warns_without_rewriting_the_basis(self):
        for streams in (["casual"], ["part_time"], ["casual", "part_time"]):
            with self.subTest(employment_streams=streams):
                profile = profile_with(
                    employment_streams=streams,
                    salary={"basis": "annual", "target_aud": 90000},
                )

                with self.profile_file(profile) as profile_path:
                    before = profile_path.read_bytes()
                    result, report = self.validate_path(profile_path)
                    self.assertEqual(profile_path.read_bytes(), before)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIs(report["valid"], True)
                self.assertEqual(report["errors"], [])
                self.assertIn(
                    "salary_basis_stream_mismatch", self.codes(report["warnings"])
                )
                self.assert_report_contains_no_inference(report, profile)

    def test_professional_stream_with_annual_basis_has_no_mismatch_warning(self):
        _, report = self.validate(
            profile_with(
                employment_streams=["professional", "casual"],
                salary={"basis": "annual", "target_aud": 120000},
            )
        )

        self.assertNotIn("salary_basis_stream_mismatch", self.codes(report["warnings"]))

    def test_profile_argument_is_required(self):
        result = self.run_validator()

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("traceback", result.stderr.lower())
        self.assertIn("--profile", result.stderr)
        self.assertIn("required", result.stderr.lower())
        self.assertEqual(result.stdout, "")

    def test_stdout_is_exactly_one_json_object_and_stderr_stays_quiet(self):
        with self.profile_file(VALID_PROFILE) as profile_path:
            result = self.run_validator("--profile", profile_path)

        stdout = result.stdout.strip()
        self.assertTrue(stdout.startswith("{"), f"stdout must be JSON: {stdout!r}")
        self.assertTrue(stdout.endswith("}"), f"stdout must be JSON: {stdout!r}")
        self.assertIsInstance(json.loads(stdout), dict)
        self.assertNotIn("traceback", result.stderr.lower())

    def test_exit_code_is_one_when_errors_are_present_and_zero_otherwise(self):
        valid_result, valid_report = self.validate(VALID_PROFILE)
        self.assertEqual(valid_result.returncode, 0)
        self.assertEqual(valid_report["errors"], [])

        invalid_result, invalid_report = self.validate(profile_without("market"))
        self.assertEqual(invalid_result.returncode, 1)
        self.assertNotEqual(invalid_report["errors"], [])

    def test_error_and_warning_messages_never_disclose_the_profile_path(self):
        with self.profile_file(
            profile_without("market"), name="private-candidate-profile.yaml"
        ) as profile_path:
            result, report = self.validate_path(profile_path)

        self.assertEqual(result.returncode, 1)
        self.assertNotEqual(report["errors"], [])
        for entry in report["errors"] + report["warnings"]:
            self.assertNotIn("private-candidate-profile", entry["message"])
            self.assertNotIn(str(profile_path), entry["message"])
        self.assertNotIn(str(profile_path), result.stderr)

    def test_empty_profile_file_is_reported_not_silently_accepted(self):
        result, report = self.validate(raw="")

        self.assertEqual(result.returncode, 1)
        self.assertIs(report["valid"], False)
        self.assertEqual(self.codes(report["errors"]), ["profile_not_a_mapping"])
        self.assertNotIn("traceback", result.stderr.lower())

    def test_workspace_template_profile_validates_without_structural_errors(self):
        template = yaml.safe_load(
            (
                ROOT / "assets" / "workspace-template" / "candidate-profile.example.yaml"
            ).read_text(encoding="utf-8")
        )

        result, report = self.validate(template)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(report["valid"], True)
        self.assertEqual(report["errors"], [])
        self.assertNotEqual(report["next_questions"], [])
        self.assert_report_contains_no_inference(report, template)

    def test_evidence_source_accepts_exactly_the_four_provenance_classes(self):
        for provenance in EVIDENCE_PROVENANCE_CLASSES:
            with self.subTest(accepted=provenance):
                result, report = self.validate(
                    profile_with(evidence_inventory=[evidence_entry(source=provenance)])
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(report["errors"], [])

        for source in REJECTED_EVIDENCE_SOURCES:
            with self.subTest(rejected=source):
                result, report = self.validate(
                    profile_with(evidence_inventory=[evidence_entry(source=source)])
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    self.codes(report["errors"]), ["evidence_provenance_malformed"]
                )
                self.assertEqual(
                    self.fields_for(report["errors"], "evidence_provenance_malformed"),
                    ["evidence_inventory[0].source"],
                )

    def test_non_finite_salary_target_is_invalid(self):
        for label, target_text in (
            ("nan", ".nan"),
            ("positive_infinity", "1.0e+400"),
            ("negative_infinity", "-1.0e+400"),
        ):
            with self.subTest(target_aud=label):
                result, report = self.validate(
                    raw=profile_raw_with_salary_target(target_text)
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("salary_target_invalid", self.codes(report["errors"]))
                self.assertEqual(
                    self.fields_for(report["errors"], "salary_target_invalid"),
                    ["salary.target_aud"],
                )
                self.assert_no_question_for(report, "salary.target_aud")

    def test_obtained_at_must_be_a_calendar_date_not_a_timestamp(self):
        for label, obtained_at_text in (
            ("timestamp", "2026-09-01 10:30:00"),
            ("basic_format_string", '"20260901"'),
        ):
            with self.subTest(obtained_at=label):
                result, report = self.validate(
                    raw=profile_raw_with_obtained_at(obtained_at_text)
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    self.codes(report["errors"]), ["evidence_provenance_malformed"]
                )
                self.assertEqual(
                    self.fields_for(report["errors"], "evidence_provenance_malformed"),
                    ["evidence_inventory[0].obtained_at"],
                )

        result, report = self.validate(raw=profile_raw_with_obtained_at("2026-09-01"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["errors"], [])

    def test_near_miss_unknown_sentinels_are_recognised_and_echoed_verbatim(self):
        for raw_value in ("Unknown", "UNKNOWN", "unknown ", " unknown"):
            with self.subTest(restrictions=raw_value):
                profile = profile_with(
                    work_rights={
                        "status": "unrestricted",
                        "restrictions": raw_value,
                        "sponsorship_required": False,
                    }
                )

                result, report = self.validate(profile)

                self.assertIn(
                    "work_rights.restrictions",
                    self.fields_for(report["warnings"], "field_unknown"),
                )
                self.assertEqual(
                    self.question_for(report, "work_rights.restrictions")["observed"],
                    raw_value,
                )
                self.assert_report_contains_no_inference(report, profile)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(report["errors"], [])

    def test_near_miss_declined_sentinels_are_recognised_and_never_re_asked(self):
        for raw_value in ("Declined", "declined "):
            with self.subTest(restrictions=raw_value):
                profile = profile_with(
                    work_rights={
                        "status": "unrestricted",
                        "restrictions": raw_value,
                        "sponsorship_required": False,
                    }
                )

                result, report = self.validate(profile)

                self.assertIn(
                    "work_rights.restrictions",
                    self.fields_for(report["warnings"], "sensitive_field_declined"),
                )
                self.assert_no_question_for(report, "work_rights.restrictions")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(report["errors"], [])

    def test_blank_entries_in_target_lists_are_invalid_values(self):
        for key in ("target_roles", "target_locations"):
            with self.subTest(field=key):
                result, report = self.validate(
                    profile_with(**{key: [None, "", "   ", 7]})
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    self.codes(report["errors"]), [f"{key}_invalid_value"] * 4
                )
                self.assertEqual(
                    self.fields_for(report["errors"], f"{key}_invalid_value"),
                    [f"{key}[{index}]" for index in range(4)],
                )
                self.assert_no_question_for(report, key)

    def test_absent_values_are_reported_missing_not_unknown_or_invented(self):
        absent_target = profile_with(salary={"basis": "annual"})
        _, absent_report = self.validate(absent_target)
        absent_codes = self.codes(absent_report["warnings"])
        self.assertIn("salary_target_missing", absent_codes)
        self.assertNotIn("salary_target_unknown", absent_codes)
        self.assertIsNone(
            self.question_for(absent_report, "salary.target_aud")["observed"]
        )
        self.assert_report_contains_no_inference(absent_report, absent_target)

        explicit_null = profile_with(salary={"basis": "annual", "target_aud": None})
        _, null_report = self.validate(explicit_null)
        null_codes = self.codes(null_report["warnings"])
        self.assertIn("salary_target_unknown", null_codes)
        self.assertNotIn("salary_target_missing", null_codes)

        absent_lists = profile_without("target_roles", "target_locations")
        _, lists_report = self.validate(absent_lists)
        for key in ("target_roles", "target_locations"):
            with self.subTest(field=key):
                self.assertIn(f"{key}_missing", self.codes(lists_report["warnings"]))
                self.assertIsNone(self.question_for(lists_report, key)["observed"])
        self.assert_report_contains_no_inference(lists_report, absent_lists)

    def test_non_regular_profile_nodes_are_unreadable_and_never_block(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = pathlib.Path(temporary_directory)
            nodes = {}
            if hasattr(os, "mkfifo"):
                fifo = directory / "fifo-profile.yaml"
                os.mkfifo(fifo)
                nodes["fifo"] = fifo
            character_device = pathlib.Path("/dev/zero")
            if character_device.exists():
                nodes["character_device"] = character_device
            nested_directory = directory / "directory-profile.yaml"
            nested_directory.mkdir()
            nodes["directory"] = nested_directory

            for label, node in nodes.items():
                with self.subTest(node=label):
                    result, report = self.validate_path(
                        node, timeout=NON_REGULAR_TIMEOUT_SECONDS
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(self.codes(report["errors"]), ["profile_unreadable"])
                    self.assertNotIn("traceback", result.stderr.lower())

    def test_oversize_profile_is_unreadable_rather_than_read_into_memory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            oversize = pathlib.Path(temporary_directory) / "oversize-profile.yaml"
            with oversize.open("wb") as handle:
                handle.seek(OVERSIZE_PROFILE_BYTES - 1)
                handle.write(b"\0")
            self.assertEqual(oversize.stat().st_size, OVERSIZE_PROFILE_BYTES)

            result, report = self.validate_path(
                oversize, timeout=NON_REGULAR_TIMEOUT_SECONDS
            )

        self.assertEqual(result.returncode, 1)
        self.assertIs(report["valid"], False)
        self.assertEqual(self.codes(report["errors"]), ["profile_unreadable"])
        self.assertNotIn("traceback", result.stderr.lower())

    def test_deeply_nested_document_is_malformed_not_an_internal_failure(self):
        raw = "market: " + "[" * NESTING_DEPTH + "]" * NESTING_DEPTH + "\n"

        result, report = self.validate(raw=raw)

        self.assertEqual(result.returncode, 1)
        self.assertIs(report["valid"], False)
        self.assertEqual(self.codes(report["errors"]), ["profile_malformed"])
        self.assertEqual(result.stderr.strip(), "")

    def test_no_inference_helper_rejects_an_observed_value_the_source_never_had(self):
        document = {"target_roles": ["Software Engineer"], "salary": {"basis": "annual"}}
        honest = {
            "valid": True,
            "errors": [],
            "warnings": [
                {
                    "code": "salary_target_missing",
                    "field": "salary.target_aud",
                    "message": "salary.target_aud is not recorded yet.",
                }
            ],
            "next_questions": [
                {
                    "field": "target_roles",
                    "question": "Which role titles should this search target?",
                    "observed": ["Software Engineer"],
                },
                {
                    "field": "salary.target_aud",
                    "question": "What target pay in AUD should this search use?",
                    "observed": None,
                },
            ],
        }

        self.assert_report_contains_no_inference(honest, document)

        rewritten = copy.deepcopy(honest)
        rewritten["next_questions"][0]["observed"] = ["Engineer"]
        with self.assertRaises(AssertionError):
            self.assert_report_contains_no_inference(rewritten, document)

        invented = copy.deepcopy(honest)
        invented["next_questions"][1]["observed"] = []
        with self.assertRaises(AssertionError):
            self.assert_report_contains_no_inference(invented, document)

        mislabelled = copy.deepcopy(honest)
        mislabelled["warnings"][0]["code"] = "salary_target_unknown"
        with self.assertRaises(AssertionError):
            self.assert_report_contains_no_inference(mislabelled, document)

    def test_candidate_location_mode_accepts_exactly_the_documented_modes(self):
        for mode in LOCATION_MODES:
            with self.subTest(accepted=mode):
                result, report = self.validate(
                    profile_with(candidate_location_mode=mode)
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(report["errors"], [])

        for value in REJECTED_CLOSED_SET_VALUES:
            with self.subTest(rejected=value):
                result, report = self.validate(
                    profile_with(candidate_location_mode=value)
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.codes(report["errors"]), ["location_mode_invalid"])
                self.assertEqual(
                    self.fields_for(report["errors"], "location_mode_invalid"),
                    ["candidate_location_mode"],
                )

        for label, profile in (
            ("null", profile_with(candidate_location_mode=None)),
            ("absent", profile_without("candidate_location_mode")),
        ):
            with self.subTest(unrecorded=label):
                result, report = self.validate(profile)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.codes(report["errors"]), ["location_mode_missing"])

    def test_employment_streams_accept_exactly_the_documented_streams(self):
        for stream in EMPLOYMENT_STREAMS:
            with self.subTest(accepted=stream):
                result, report = self.validate(
                    profile_with(
                        employment_streams=[stream],
                        salary={"basis": "hourly", "target_aud": 45},
                    )
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(report["errors"], [])

        for value in REJECTED_CLOSED_SET_VALUES:
            with self.subTest(rejected=value):
                result, report = self.validate(profile_with(employment_streams=[value]))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    self.codes(report["errors"]), ["employment_streams_invalid_value"]
                )
                self.assertEqual(
                    self.fields_for(report["errors"], "employment_streams_invalid_value"),
                    ["employment_streams[0]"],
                )

        for label, profile in (
            ("null", profile_with(employment_streams=None)),
            ("absent", profile_without("employment_streams")),
        ):
            with self.subTest(unrecorded=label):
                result, report = self.validate(profile)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    self.codes(report["errors"]), ["employment_streams_missing"]
                )

    def test_market_accepts_exactly_the_australian_string(self):
        for value in ("australia", "Australia ", "AUSTRALIA", "AU", "unknown", 7):
            with self.subTest(rejected=value):
                result, report = self.validate(profile_with(market=value))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.codes(report["errors"]), ["market_not_australia"])
                self.assertEqual(
                    self.fields_for(report["errors"], "market_not_australia"), ["market"]
                )

        for label, profile in (
            ("null", profile_with(market=None)),
            ("absent", profile_without("market")),
        ):
            with self.subTest(unrecorded=label):
                result, report = self.validate(profile)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.codes(report["errors"]), ["market_missing"])
                self.assertEqual(
                    self.fields_for(report["errors"], "market_missing"), ["market"]
                )

    def test_salary_must_be_a_mapping_that_records_a_basis(self):
        cases = (
            ("scalar", "annual", "salary_not_a_mapping", "salary"),
            (
                "without_basis",
                {"target_aud": 150000},
                "salary_basis_missing",
                "salary.basis",
            ),
        )

        for label, salary, code, field in cases:
            with self.subTest(salary=label):
                result, report = self.validate(profile_with(salary=salary))
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.codes(report["errors"]), [code])
                self.assertEqual(self.fields_for(report["errors"], code), [field])

    def test_salary_target_must_be_a_positive_number_when_it_is_recorded(self):
        for target in (0, -1, "1500", [150000], {"aud": 150000}):
            with self.subTest(target_aud=target):
                result, report = self.validate(
                    profile_with(salary={"basis": "annual", "target_aud": target})
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(self.codes(report["errors"]), ["salary_target_invalid"])
                self.assertEqual(
                    self.fields_for(report["errors"], "salary_target_invalid"),
                    ["salary.target_aud"],
                )

    def test_work_rights_restrictions_must_be_non_empty_text(self):
        for restrictions in ("", "   ", 7):
            with self.subTest(restrictions=restrictions):
                result, report = self.validate(
                    profile_with(
                        work_rights={
                            "status": "unrestricted",
                            "restrictions": restrictions,
                            "sponsorship_required": False,
                        }
                    )
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    self.codes(report["errors"]), ["work_rights_restrictions_invalid"]
                )
                self.assertEqual(
                    self.fields_for(
                        report["errors"], "work_rights_restrictions_invalid"
                    ),
                    ["work_rights.restrictions"],
                )

    def test_work_rights_sponsorship_required_is_boolean_or_a_sentinel(self):
        for sponsorship_required in (True, False, "unknown", "declined"):
            with self.subTest(accepted=sponsorship_required):
                result, report = self.validate(
                    profile_with(
                        work_rights={
                            "status": "unrestricted",
                            "restrictions": "none",
                            "sponsorship_required": sponsorship_required,
                        }
                    )
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(report["errors"], [])

        for label, raw_text in (("yes", "yes"), ("no", "no")):
            with self.subTest(unquoted=label):
                result, report = self.validate(
                    raw=profile_raw_with_sponsorship_required(raw_text)
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(report["errors"], [])

        for sponsorship_required in ("yes", "true", "", 1):
            with self.subTest(rejected=sponsorship_required):
                result, report = self.validate(
                    profile_with(
                        work_rights={
                            "status": "unrestricted",
                            "restrictions": "none",
                            "sponsorship_required": sponsorship_required,
                        }
                    )
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    self.codes(report["errors"]),
                    ["work_rights_sponsorship_required_invalid"],
                )
                self.assertEqual(
                    self.fields_for(
                        report["errors"], "work_rights_sponsorship_required_invalid"
                    ),
                    ["work_rights.sponsorship_required"],
                )

    def test_unrecognised_top_level_keys_are_warned_rather_than_dropped(self):
        profile = profile_with(candidate_locaton_mode="offshore", notes="call me Friday")

        result, report = self.validate(profile)

        self.assertEqual(
            sorted(self.fields_for(report["warnings"], "unrecognised_field")),
            ["candidate_locaton_mode", "notes"],
        )
        self.assert_no_question_for(report, "candidate_locaton_mode")
        self.assert_no_question_for(report, "notes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["errors"], [])

        _, recognised = self.validate(VALID_PROFILE)
        self.assertNotIn("unrecognised_field", self.codes(recognised["warnings"]))

    def test_duplicate_evidence_ids_break_traceability_and_are_an_error(self):
        result, report = self.validate(
            profile_with(
                evidence_inventory=[
                    evidence_entry(),
                    evidence_entry(claim="Ran the release train"),
                ]
            )
        )

        self.assertEqual(result.returncode, 1)
        self.assertIs(report["valid"], False)
        self.assertIn("evidence_id_duplicated", self.codes(report["errors"]))
        self.assertEqual(
            self.fields_for(report["errors"], "evidence_id_duplicated"),
            ["evidence_inventory[1].id"],
        )

        _, distinct = self.validate(
            profile_with(
                evidence_inventory=[
                    evidence_entry(),
                    evidence_entry(id="ev-2", claim="Ran the release train"),
                ]
            )
        )
        self.assertNotIn("evidence_id_duplicated", self.codes(distinct["errors"]))

    def test_validate_profile_never_mutates_the_callers_document(self):
        original_path = list(sys.path)
        sys.path.insert(0, str(SCRIPTS_DIRECTORY))
        try:
            import validate_profile
        finally:
            sys.path[:] = original_path

        documents = {
            "clean": copy.deepcopy(VALID_PROFILE),
            "noisy": profile_with(
                target_roles=[],
                employment_streams=["contract"],
                salary={"basis": "annual", "target_aud": "unknown"},
                work_rights={
                    "status": "unknown",
                    "restrictions": "declined",
                    "sponsorship_required": "unknown",
                },
                evidence_inventory=[evidence_entry(source="hearsay")],
            ),
        }

        for label, document in documents.items():
            with self.subTest(document=label):
                pristine = copy.deepcopy(document)

                report = validate_profile.validate_profile(document).as_dict()

                self.assertEqual(
                    document,
                    pristine,
                    "validate_profile must not touch the caller's document",
                )
                self.assertEqual(set(report), REPORT_KEYS)

        noisy = validate_profile.validate_profile(documents["noisy"]).as_dict()
        self.assertNotEqual(noisy["errors"], [])
        self.assertNotEqual(noisy["warnings"], [])
        self.assertNotEqual(noisy["next_questions"], [])


if __name__ == "__main__":
    unittest.main()
