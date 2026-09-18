"""Task 8 reference contracts; standard-library-only, read-only document tests.

GREEN authoring convention: ordinary Markdown headings/lists for procedures,
pipe tables for finite contracts. Tables use semantic columns (factor/weight,
verdict/top_n, provenance/meaning, metric/numerator/denominator/days/anchor,
field/meaning, sheet/purpose, input/purpose). Column aliases below allow common
variants, extra explanatory columns are welcome, and headings/prose are flexible.
Workflow checks require order and rule checks keep related concepts in one
paragraph or list item; mere mentions scattered across a document do not pass.
These tests check documented instructions, not whether an LLM will obey them.
"""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
REFERENCES = (
    "onboarding", "australian-work-rights", "search-filters",
    "job-screening-and-scoring", "resume-and-cover-letter",
    "casual-and-part-time", "assisted-application", "tracker-and-analytics",
    "privacy-and-consent",
)
PROFESSIONAL = {
    "core capability match": 25,
    "evidence in candidate profile or resume": 20,
    "responsibility and seniority fit": 15,
    "work rights and location": 15,
    "industry transferability": 10,
    "employment preferences": 5,
    "application competitiveness": 5,
    "career value": 5,
}
CASUAL = {
    "availability match": 25, "work-rights compatibility": 20,
    "commute practicality": 15, "relevant experience": 15,
    "pay and conditions": 10, "required certificates": 10,
    "employer legitimacy": 5,
}
PROVENANCE = {
    "candidate_statement", "candidate_document", "third_party_document",
    "verified_third_party",
}
VERDICTS = {
    "apply_first": "yes", "apply_with_targeted_edits": "yes",
    "stretch": "yes", "do_not_apply": "no",
}
# Values are the approved formulas, including cohort anchors used by Task 7.
METRICS = {
    "application_rate": ("submitted", "unique_actionable_recommendations", "0", "none"),
    "employer_response_rate": ("employer_responses", "submitted_mature_cohort", "30", "applied_at"),
    "application_to_interview_rate": ("interviews", "submitted_mature_cohort", "30", "applied_at"),
    "interview_to_offer_rate": ("offers", "interviews_mature_cohort", "30", "interview_at"),
    "application_to_offer_rate": ("offers", "submitted_offer_mature_cohort", "60", "applied_at"),
}
ALIASES = {
    "factor": {"factor", "criterion", "dimension"},
    "weight": {"weight", "default weight", "weight (%)", "default weight (%)"},
    "verdict": {"verdict", "verdict enum"},
    "top_n": {"top_n", "top n", "top n eligible", "eligible for top n"},
    "provenance": {"provenance", "evidence provenance", "provenance enum", "evidence type"},
    "metric": {"metric", "metric name"},
    "days": {"days", "default days", "maturity days", "maturity_window_days"},
    "anchor": {"anchor", "cohort anchor", "maturity anchor"},
    "field": {"field", "output field"},
    "sheet": {"sheet", "sheet name", "dashboard sheet"},
    "input": {"input", "input file", "source file"},
}


def clean(value):
    return re.sub(r"\s+", " ", re.sub(r"[`*]", "", value)).strip()


def tables(markdown):
    """Yield Markdown tables, ignoring separator rows and presentation markup."""
    groups, current = [], []
    for line in markdown.splitlines() + [""]:
        if line.strip().startswith("|"):
            cells = [clean(c) for c in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-+:?", c.replace(" ", "")) for c in cells):
                current.append(cells)
        elif current:
            groups.append(current)
            current = []
    return groups


class ReferenceContractTests(unittest.TestCase):
    def reference(self, name):
        path = ROOT / "references" / f"{name}.md"
        self.assertTrue(path.is_file(), f"Missing Task 8 reference: references/{name}.md")
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.strip(), f"Empty Task 8 reference: {path.name}")
        return text

    def contract_table(self, text, *columns):
        matches = []
        for table in tables(text):
            headers = [c.lower() for c in table[0]]
            indexes = []
            for column in columns:
                hits = [i for i, h in enumerate(headers) if h in ALIASES.get(column, {column})]
                if len(hits) != 1:
                    break
                indexes.append(hits[0])
            else:
                self.assertTrue(all(len(row) == len(headers) for row in table[1:]),
                                f"Malformed {columns} table row")
                matches.append([tuple(row[i] for i in indexes) for row in table[1:]])
        self.assertEqual(len(matches), 1, f"Expected one contract table with columns {columns}")
        return matches[0]

    def keyed(self, rows):
        result = {row[0]: row[1:] for row in rows}
        self.assertEqual(len(result), len(rows), "Duplicate contract key")
        return result

    def rule(self, text, *patterns):
        """All concepts must occur together in one paragraph or list item."""
        blocks = re.split(r"\n\s*\n|\n(?=\s*(?:[-*]|\d+[.)])\s)", clean_lines(text))
        self.assertTrue(any(all(re.search(p, block, re.I) for p in patterns) for block in blocks),
                        f"Missing operational rule relating: {patterns}")

    def ordered(self, text, *patterns):
        position = 0
        text = clean_lines(text)
        for pattern in patterns:
            match = re.search(pattern, text[position:], re.I)
            self.assertIsNotNone(match, f"Missing or out-of-order workflow step: {pattern}")
            position += match.end()

    def weights(self, text, expected):
        rows = self.contract_table(text, "factor", "weight")
        parsed = {}
        for factor, weight in rows:
            self.assertRegex(weight, r"^\d+(?:\s*(?:%|percent))?$", "Weight must be numeric percent")
            parsed[factor.lower()] = int(re.match(r"\d+", weight)[0])
        self.assertEqual(len(parsed), len(rows), "Duplicate scoring factor")
        self.assertEqual(parsed, expected)
        self.assertEqual(sum(parsed.values()), 100)
        self.rule(text, r"candidate", r"weights?", r"override|customi[sz]e|replace")

    def test_all_nine_references_exist(self):
        missing = [f"references/{name}.md" for name in REFERENCES
                   if not (ROOT / "references" / f"{name}.md").is_file()]
        self.assertEqual(missing, [], "Missing Task 8 references")

    def test_onboarding_orders_intake_and_confirmation_gates(self):
        text = self.reference("onboarding")
        self.ordered(text, r"first[- ]use|existing profile", r"professional",
                     r"casual|part[- ]time", r"interview", r"resume draft",
                     r"candidate profile summary", r"confirm.{0,50}profile|profile.{0,50}confirm",
                     r"search filters?", r"confirm.{0,50}filters?|filters?.{0,50}confirm", r"discovery")
        self.rule(text, r"existing profile", r"detect|check|load|reuse|review")
        self.rule(text, r"resume", r"read|review|parse", r"no resume|without.{0,20}resume|missing", r"interview", r"draft")
        self.rule(text, r"profile", r"confirm", r"before|only after", r"filters?")
        self.rule(text, r"filters?", r"confirm", r"before|only after", r"discovery")
        self.rule(text, r"unknown|missing", r"facts?", r"never|do not|must not|cannot", r"assum|infer|guess")

    def test_work_rights_stay_unknown_until_supported_in_australian_market(self):
        text = self.reference("australian-work-rights")
        self.rule(text, r"australia(?:n)?", r"only|exclusively")
        self.rule(text, r"onshore", r"offshore", r"confirm|distinguish|record")
        self.rule(text, r"work rights", r"unknown", r"until", r"confirm|evidence")
        self.rule(text, r"factual|facts", r"verif", r"not|no|avoid", r"immigration.{0,20}advice")
        self.rule(text, r"visa|permanent residen|\bPR\b", r"never|must not|do not|no", r"guarantee")
        self.rule(text, r"visa|policy", r"never|must not|do not|no", r"hard.?cod|frozen")

    def test_platform_filters_are_candidate_configurable_and_runtime_checked(self):
        text = self.reference("search-filters")
        self.rule(text, r"SEEK", r"LinkedIn", r"Indeed", r"filter|map")
        self.rule(text, r"candidate", r"configur|edit|customi[sz]", r"Top N")
        self.rule(text, r"platform|site", r"controls?|filters?", r"runtime|current|live", r"verif|check")

    def test_search_filters_requires_pipeline_and_verification_before_fit(self):
        text = self.reference("search-filters")
        self.assertRegex(clean_lines(text), r"Discovery\s*(?:->|→)\s*Verification\s*(?:->|→)\s*Hard Gate\s*(?:->|→)\s*Top N")
        self.rule(text, r"verif", r"before|only after", r"expensive|full|detailed", r"fit|analysis")
        self.rule(text, r"Top N", r"only after|not.{0,30}before|never.{0,30}before", r"gates?", r"pass|complete")

    def test_discovery_verifies_before_expensive_analysis_and_never_pads(self):
        text = self.reference("job-screening-and-scoring")
        self.assertRegex(clean_lines(text), r"Discovery\s*(?:->|→)\s*Verification\s*(?:->|→)\s*Hard Gate\s*(?:->|→)\s*Top N")
        self.rule(text, r"verif", r"before|only after", r"expensive|full|detailed", r"fit|analysis")
        self.rule(text, r"Top N", r"upper bound|at most|maximum", r"never pad|do not pad|no padding|must not pad")
        self.rule(text, r"canonical_job_id", r"dedup|duplicate", r"cross[- ]platform|across.{0,30}platform")
        self.rule(text, r"shortlist|Top N", r"snapshot", r"source URL", r"retrieval|retrieved")

    def test_professional_default_weights_and_override(self):
        self.weights(self.reference("job-screening-and-scoring"), PROFESSIONAL)

    def test_verdict_enum_and_top_n_eligibility_are_exact(self):
        text = self.reference("job-screening-and-scoring")
        actual = self.keyed(self.contract_table(text, "verdict", "top_n"))
        self.assertEqual(actual, {k: (v,) for k, v in VERDICTS.items()})
        self.rule(text, r"hard block", r"cannot|must not|never", r"offset|outweigh|compensat", r"score")
        self.rule(text, r"score", r"not|never", r"interview probabilit")

    def test_shortlist_has_independent_hr_and_hiring_manager_views(self):
        text = self.reference("job-screening-and-scoring")
        self.rule(text, r"\bHR\b", r"Hiring Manager", r"independent|separate", r"evidence|snapshot")
        self.rule(text, r"shortlist|recommend|each job", r"gaps", r"blockers", r"evidence confidence")

    def test_provenance_enum_is_exact_and_old_enums_cannot_return(self):
        text = self.reference("job-screening-and-scoring")
        rows = self.contract_table(text, "provenance")
        self.assertEqual(set(self.keyed(rows)), PROVENANCE)
        # Inspect every provenance-bearing table, not just the primary definition.
        for name in REFERENCES:
            for table in tables(self.reference(name)):
                for index, header in enumerate(table[0]):
                    if re.search(r"provenance|evidence type", header, re.I):
                        values = {r[index] for r in table[1:] if len(r) > index}
                        self.assertTrue(values <= PROVENANCE,
                                        f"{name}: obsolete/additional provenance enum: {values - PROVENANCE}")
            # Alternate list-style enum declarations must not restore old labels.
            doc = clean_lines(self.reference(name))
            sections = re.split(r"(?m)^#{1,6}\s+", doc)
            for section in sections:
                heading, _, body = section.partition("\n")
                if re.search(r"provenance|evidence (?:taxonomy|types?)", heading, re.I):
                    values = re.findall(r"(?m)^[-]\s+([a-z_]+)(?:\s*[:—–-]|\s*$)", body)
                    self.assertFalse(set(values) & {"resume", "candidate_confirmed", "documented", "unknown"},
                                     f"{name}: obsolete list-style provenance enum")
            self.assertNotRegex(doc, r"(?im)^(?:[-*] )?(?:provenance|evidence type)(?: enum| values)?\s*[:=][^\n]*\b(?:resume|candidate_confirmed|documented|unknown)\b")

    def test_evidence_audit_forbids_inflation_and_preserves_hard_gaps(self):
        text = self.reference("resume-and-cover-letter")
        self.assertIn("Evidence Audit", text)
        for item in (r"metrics", r"years", r"tools", r"responsibilities", r"work rights", r"salary", r"declarations"):
            with self.subTest(claim=item):
                self.rule(text, r"never|must not|do not|prohibit", r"fabricat|inflat|invent|guess", item)
        self.rule(text, r"hard[- ]requirement|mandatory requirement", r"gaps?", r"remain|preserve|must stay", r"not|never", r"transferable|disguise")

    def test_tailoring_preserves_master_and_audits_before_candidate_approval(self):
        text = self.reference("resume-and-cover-letter")
        self.rule(text, r"master resume", r"preserve|retain|keep", r"tailor|job[- ]specific")
        self.rule(text, r"claims?", r"only|every|each", r"support|evidence")
        self.rule(text, r"canonical_job_id", r"jd_snapshot_path", r"document.version|document_versions", r"link|trace|record")
        self.ordered(text, r"candidate.{0,30}(?:select|choos)", r"tailor", r"Evidence Audit", r"unsupported", r"candidate.{0,30}(?:approv|confirm)")
        self.rule(text, r"unsupported", r"remov|surface|needs_confirmation")
        self.rule(text, r"candidate approval", r"not|never|doesn't", r"validat|evidence|fact")

    def test_casual_weights_and_all_role_specific_checks(self):
        text = self.reference("casual-and-part-time")
        self.weights(text, CASUAL)
        for topic in (r"availability", r"work rights", r"commute", r"experience", r"pay", r"certificates|licen[cs]es", r"legitimacy", r"award|classification"):
            self.assertRegex(clean_lines(text).lower(), topic.lower())
        self.rule(text, r"wage|rate", r"no|never|must not|do not", r"hard.?cod|frozen")
        self.assertNotRegex(text, r"(?:AUD\s*|\$)\d+(?:\.\d+)?\s*(?:/\s*h|per hour|an hour)")

    def test_assisted_apply_authorization_and_final_submit_boundary(self):
        text = self.reference("assisted-application")
        self.ordered(text, r"candidate.{0,40}(?:select|choos)", r"explicit.{0,50}authori[sz]",
                     r"ordinary fields", r"pause", r"review", r"STOP BEFORE FINAL SUBMIT",
                     r"candidate.{0,30}submit", r"confirm.{0,60}(?:actual|successful).{0,30}submission")
        self.rule(text, r"explicit", r"authori[sz]", r"one job|single job|job[- ]specific")
        self.rule(text, r"ordinary fields", r"confirm", r"candidate")
        for category in ("unknown", "sensitive", "legal", "declaration"):
            self.rule(text, r"pause|stop", category)
        self.rule(text, r"submitted", r"only after", r"candidate", r"confirm", r"actual|successful")

    def test_assisted_apply_prohibits_unsafe_shortcuts(self):
        text = self.reference("assisted-application")
        for shortcut in (r"auto(?:matic)?", r"bulk", r"silent"):
            self.rule(text, r"never|must not|do not|prohibit|no", shortcut, r"submit|submission")
        self.rule(text, r"never|must not|do not|no", r"guess", r"declarations?")

    def test_tracker_keeps_current_state_history_and_snapshot_traceability(self):
        text = self.reference("tracker-and-analytics")
        self.rule(text, r"applications\.csv", r"current[- ]state|current state")
        self.rule(text, r"events\.jsonl", r"append[- ]only", r"source of truth")
        self.rule(text, r"snapshot", r"canonical_job_id", r"jd_snapshot_path", r"document.version|document_versions", r"event")
        for field in ("first_response_at", "next_action", "action_due_at", "blocker_category"):
            self.assertRegex(text, rf"\b{field}\b")
        self.rule(text, r"screening_status", r"application_stage", r"action_status", r"separate|independent|distinct")

    def test_responses_require_explicit_response_evidence_not_lifecycle_proxies(self):
        text = self.reference("tracker-and-analytics")
        self.rule(text, r"response", r"only", r"explicit", r"first_response_at")
        self.rule(text, r"never|must not|do not|no", r"infer|proxy|proxies", r"response", r"interview", r"offer", r"reject", r"withdraw", r"accept")

    def test_five_formulas_and_maturity_anchors_are_exact(self):
        text = self.reference("tracker-and-analytics")
        actual = self.keyed(self.contract_table(text, "metric", "numerator", "denominator", "days", "anchor"))
        self.assertEqual(actual, METRICS)
        self.rule(text, r"numerator", r"cohort|denominator", r"same|within|only")
        self.rule(text, r"terminal", r"rejected", r"withdrawn", r"accepted", r"mature|maturity", r"or|regardless|even|without waiting")
        self.rule(text, r"overrid|custom|chang", r"window", r"not|never|preserv", r"denominator")

    def test_metric_output_and_small_sample_boundary(self):
        text = self.reference("tracker-and-analytics")
        self.assertEqual(set(self.keyed(self.contract_table(text, "field"))),
                         {"numerator", "denominator", "maturity_window", "rate", "data_status"})
        self.rule(text, r"maturity_window", r"days", r"terminal_outcome_is_mature", r"true")
        self.rule(text, r"rate", r"numerator\s*/\s*denominator")
        self.rule(text, r"denominator", r"0|zero", r"rate", r"null|None")
        self.rule(text, r"segment|sample", r"<\s*10|below 10|fewer than 10", r"insufficient_data", r"no strong|must not.{0,30}strong|do not.{0,30}strong")
        self.rule(text, r"canonical_job_id", r"dedup|unique", r"count|metric|analytics")

    def test_dashboard_has_exact_inputs_sheets_and_is_disposable(self):
        text = self.reference("tracker-and-analytics")
        sheets = self.contract_table(text, "sheet")
        self.assertEqual([r[0] for r in sheets], ["Overview", "Applications", "Conversion", "Follow-ups"])
        inputs = self.contract_table(text, "input")
        self.assertEqual(set(self.keyed(inputs)),
                         {"tracker/applications.csv", "tracker/events.jsonl", "reports/analytics-summary.json"})
        self.rule(text, r"dashboard", r"disposable", r"read[- ]only", r"presentation")
        self.rule(text, r"workbook|dashboard", r"no|never|must not|do not", r"write.?back")
        self.rule(text, r"summary", r"missing", r"invalid", r"fail closed|reject|abort", r"no|never|do not|must not", r"fallback|recomput|recalculat")
        self.rule(text, r"source", r"trace|path|link", r"metric|row|sheet")

    def test_privacy_is_local_and_public_examples_are_synthetic(self):
        text = self.reference("privacy-and-consent")
        self.rule(text, r"local", r"default")
        self.rule(text, r"public", r"examples?", r"only", r"synthetic")
        for private in (r"PII|personal.{0,20}identif", r"credentials", r"private documents", r"workspace artifacts", r"private absolute paths", r"raw.{0,20}answers"):
            self.rule(text, r"never|must not|do not|no|prohibit", r"public|export|share|commit", private)

    def test_cross_user_and_sensitive_dimensions_require_separate_consent(self):
        text = self.reference("privacy-and-consent")
        self.rule(text, r"cross[- ]user", r"disabled", r"default", r"separate", r"opt[- ]in")
        self.rule(text, r"salary", r"separate", r"consent|opt[- ]in", r"band")
        self.rule(text, r"visa|work[- ]rights|work rights", r"separate", r"consent|opt[- ]in", r"broad|categor")
        self.rule(text, r"exact salary", r"visa", r"local", r"remain|keep|stay")
        self.rule(text, r"no|never|must not|do not", r"omnibus|bundl|blanket", r"consent")
        self.rule(text, r"cross[- ]user|analytics", r"anonymous", r"aggregat", r"separate", r"explicit", r"opt[- ]in")
        self.rule(text, r"never|must not|do not|no", r"export|share", r"identifiable data")

    def test_each_schedule_requires_its_own_authorization_and_no_change_is_quiet(self):
        text = self.reference("privacy-and-consent")
        for workflow in (r"Morning Discovery", r"Evening Check[- ]in"):
            self.rule(text, workflow, r"explicit", r"authori[sz]|consent", r"separate|own")
        self.rule(text, r"automation|schedule", r"analytics", r"distinct|separate|does not|not authori")
        self.rule(text, r"no|never|must not|do not", r"automatic", r"schedul|enabl")
        self.rule(text, r"no actionable change", r"quiet|silent")
        self.rule(text, r"Evening Check[- ]in", r"no|never|must not|does not", r"new discovery")

    def test_runtime_sources_are_authoritative_and_policy_is_not_frozen(self):
        sources = {
            "australian-work-rights": ("Department of Home Affairs", "VEVO"),
            "casual-and-part-time": ("Fair Work Ombudsman",),
            "search-filters": ("SEEK Australia", "LinkedIn Help", "Indeed Australia Help"),
        }
        for name, authorities in sources.items():
            text = self.reference(name)
            for authority in authorities:
                with self.subTest(reference=name, authority=authority):
                    self.rule(text, re.escape(authority), r"runtime|current|live", r"check|verif|consult")
            self.rule(text, r"policy|rules|rates|controls", r"no|never|must not|do not", r"frozen|hard.?cod")

    def test_canonical_terminology_is_used_in_each_owning_workflow(self):
        ownership = {
            "job-screening-and-scoring": ("canonical_job_id", "jd_snapshot_path", "Evidence Audit", "screening_status"),
            "resume-and-cover-letter": ("canonical_job_id", "jd_snapshot_path", "Evidence Audit"),
            "assisted-application": ("application_stage", "action_status"),
            "tracker-and-analytics": ("canonical_job_id", "jd_snapshot_path", "screening_status", "application_stage", "action_status", "first_response_at", "action_due_at", "insufficient_data"),
        }
        for name, terms in ownership.items():
            text = self.reference(name)
            for term in terms:
                with self.subTest(reference=name, term=term):
                    self.assertRegex(text, rf"\b{re.escape(term)}\b")
            self.assertNotRegex(text, r"\b(?:canonicalJobId|jdSnapshotPath|firstResponseAt|actionDueAt)\b")

    def test_skill_routes_to_nine_live_references(self):
        contents = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Reference Routing Boundary", contents)
        routing = contents.split("## Reference Routing Boundary", 1)[1].split("\n## ", 1)[0]
        expected = {
            "references/onboarding.md": "Candidate interview, profile fields, resume intake, and profile confirmation",
            "references/australian-work-rights.md": "Current official-source checks, onshore/offshore distinctions, sponsorship, and legal-advice limits",
            "references/search-filters.md": "Candidate-editable filters and live platform mapping",
            "references/job-screening-and-scoring.md": "Discovery, verification, deduplication, hard gates, ranking, progressive retrieval, and HR/Hiring Manager outputs",
            "references/resume-and-cover-letter.md": "Evidence-grounded tailoring, Evidence Audit, and document-to-JD linkage",
            "references/casual-and-part-time.md": "Availability, rates, awards, physical requirements, and role-specific licences",
            "references/assisted-application.md": "Job-specific form assistance, candidate review, safeguards, and the no-submit boundary",
            "references/tracker-and-analytics.md": "Current-state tracking, append-only events, snapshots, document traceability, and personal analytics",
            "references/privacy-and-consent.md": "Local data boundaries, sensitive data, automation consent, and anonymous analytics consent",
        }
        rows = [tuple(cell.strip().strip("`") for cell in line.strip().strip("|").split("|"))
                for line in routing.splitlines() if line.lstrip().startswith("| `references/")]
        self.assertEqual(len(rows), len(expected))
        self.assertEqual(dict(rows), expected, "Preserve the routing paths and ownership mappings")
        for path in expected:
            self.assertTrue((ROOT / path).is_file(), f"Routing target is not live: {path}")
        self.assertIn("`SKILL.md` owns mode selection, orchestration, safety, consent, and gate rules.", routing)
        self.assertRegex(routing, r"read only.*(?:needed|required).*workflow")
        self.assertNotRegex(routing.lower(), r"future|nonexistent|not (?:yet )?(?:part of|in) the current repository shell",
                            "Live references must not be described as future or absent")

    def test_scope_does_not_invent_hosted_cross_user_service(self):
        text = self.reference("privacy-and-consent")
        self.rule(text, r"cross[- ]user", r"backend|hosted service", r"out of scope|not provided|must not|do not|no")


def clean_lines(text):
    # Preserve block boundaries while ignoring Markdown emphasis/backticks.
    return "\n".join(clean(line) for line in text.splitlines())


class MarkdownHelperTests(unittest.TestCase):
    """Tiny in-memory checks distinguish a valid RED from a broken test harness."""

    def test_tables_ignore_markup_and_separator_and_keep_identifiers(self):
        self.assertEqual(tables("| **field** | meaning |\n| :--- | ---: |\n| `first_response_at` | Explicit response |"),
                         [[["field", "meaning"], ["first_response_at", "Explicit response"]]])

    def test_contract_table_accepts_aliases_and_explanatory_columns(self):
        case = ReferenceContractTests()
        self.assertEqual(case.contract_table("| criterion | default weight (%) | rationale |\n| --- | --- | --- |\n| capability | 25% | relevant |", "factor", "weight"),
                         [("capability", "25%")])
        with self.assertRaisesRegex(AssertionError, "Malformed"):
            case.contract_table("| factor | weight |\n| --- | --- |\n| missing weight |", "factor", "weight")

    def test_rules_require_related_concepts_in_same_block(self):
        case = ReferenceContractTests()
        case.rule("Never guess unknown declarations.", r"never", r"guess", r"declarations")
        with self.assertRaises(AssertionError):
            case.rule("Never guess names.\n\nDeclarations are fine.", r"never", r"guess", r"declarations")

    def test_workflow_requires_order_not_just_presence(self):
        case = ReferenceContractTests()
        case.ordered("Confirm profile. Confirm filters. Begin Discovery.", "profile", "filters", "Discovery")
        with self.assertRaises(AssertionError):
            case.ordered("Discovery first. Confirm filters. Confirm profile.", "profile", "filters", "Discovery")


if __name__ == "__main__":
    unittest.main()
