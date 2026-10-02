"""Focused Task 9 behavioral contracts for durable reference guidance.

These read-only, standard-library tests verify that each safety behavior is
documented as one operational rule. They do not encode scenario results or
invoke an LLM.
"""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def reference(name):
    path = ROOT / "references" / f"{name}.md"
    return path.read_text(encoding="utf-8")


def clean(value):
    return re.sub(r"\s+", " ", re.sub(r"[`*]", "", value)).strip()


def blocks(markdown):
    """Return paragraphs and individual Markdown list items as clean blocks."""
    text = "\n".join(clean(line) for line in markdown.splitlines())
    return re.split(r"\n\s*\n|\n(?=\s*(?:[-*]|\d+[.)])\s)", text)


class Task9BehaviorContractTests(unittest.TestCase):
    def assert_rule(self, document, *patterns):
        text = reference(document)
        self.assertTrue(
            any(all(re.search(pattern, block, re.I) for pattern in patterns)
                for block in blocks(text)),
            f"{document}: missing one operational rule relating {patterns}",
        )

    def assert_ordered_rule(self, document, *patterns):
        """Require one operational block to state each action in order."""
        for block in blocks(reference(document)):
            position = 0
            for pattern in patterns:
                match = re.search(pattern, block[position:], re.I)
                if match is None:
                    break
                position += match.end()
            else:
                return
        self.fail(f"{document}: missing ordered operational rule {patterns}")

    def test_top_level_profile_confirmation_records_pending_transition(self):
        contents = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        section = contents.split("## Required Confirmations", 1)[1].split("\n## ", 1)[0]
        patterns = (
            r"before.{0,30}(?:ask|request).{0,30}confirm|prior to.{0,30}confirm",
            r"current[- ]state profile",
            r"actual confirmation state|pending",
            r"append",
            r"intake|confirmation",
            r"event",
        )
        self.assertTrue(
            any(all(re.search(pattern, block, re.I) for pattern in patterns)
                for block in blocks(section)),
            "SKILL.md must route profile confirmation through the durable onboarding transition",
        )

    def test_top_level_profile_confirmation_includes_explicit_stream(self):
        contents = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        section = contents.split("## Required Confirmations", 1)[1].split("\n## ", 1)[0]
        self.assertRegex(section, r"(?i)confirm.{0,80}(?:intended |employment |work )?stream")
        self.assertRegex(section, r"(?i)Professional")
        self.assertRegex(section, r"(?i)Casual/Part-time|casual or part-time")

    def test_scr_05_deduplicates_to_one_canonical_opportunity_before_ranking(self):
        self.assert_rule(
            "job-screening-and-scoring",
            r"canonical (?:job|opportunit)|canonical_job_id",
            r"deduplicat|duplicate",
            r"pass(?:ed)?(?: it| that opportunity)? forward|advance|continue|enter.{0,30}ranking",
            r"screen|rank",
            r"count.{0,30}(?:once|one)|not.{0,30}separately",
            r"candidate fit",
            r"unavailable|unknown|missing",
            r"do not|never|must not",
            r"fabricat|invent|infer|guess",
        )

    def test_scr_05_one_canonical_candidate_enters_ranking_without_top_n_admission(self):
        self.assert_rule(
            "job-screening-and-scoring",
            r"deterministic.{0,30}deduplicat|deduplicat.{0,30}deterministic",
            r"exactly one",
            r"canonical (?:job|opportunit)|canonical_job_id",
            r"ranking (?:input|candidate set)",
            r"not|does not|must not",
            r"Top N admission|fit verdict",
            r"Verification",
            r"Hard Gate",
            r"control|govern|gate",
            r"Top N",
        )

    def test_onboarding_does_not_infer_location_mode_from_city_of_residence(self):
        self.assert_rule(
            "onboarding",
            r"onshore|offshore|location mode",
            r"residen|city|location",
            r"does not|must not|never",
            r"infer|assume",
            r"confirm|ask",
        )

    def test_onboarding_updates_current_state_and_appends_intake_event(self):
        self.assert_rule(
            "onboarding",
            r"onboarding|intake|profile",
            r"current[- ]state",
            r"append",
            r"event",
            r"confirmation|pending|confirmed",
        )

    def test_scr_05_top_level_orchestration_passes_one_canonical_ranking_candidate(self):
        contents = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        section = contents.split("## Orchestration Gates", 1)[1].split("\n## ", 1)[0]
        patterns = (
            r"deterministic",
            r"duplicates?",
            r"exactly one",
            r"canonical opportunit",
            r"enter.{0,30}ranking (?:input|candidate set)",
            r"does not|must not|not",
            r"bypass",
            r"Verification",
            r"Hard Gate",
            r"admit",
            r"scoring|Top N",
        )
        self.assertTrue(
            any(all(re.search(pattern, block, re.I) for pattern in patterns)
                for block in blocks(section)),
            "SKILL.md: Orchestration Gates must preserve the canonical ranking-input transition",
        )

    def test_safe_01_refusal_immediately_supplies_truthful_replacement_wording(self):
        self.assert_rule(
            "resume-and-cover-letter",
            r"unsupported claim",
            r"refus|remove|reject",
            r"immediate|same response",
            r"truthful (?:replacement|alternative).{0,20}wording|replacement wording",
            r"grounded|supported",
            r"evidence",
        )

    def test_safe_01_replacement_does_not_add_unverified_activities(self):
        self.assert_rule(
            "resume-and-cover-letter",
            r"replacement wording",
            r"only|exact",
            r"verified|supported",
            r"facts|claims",
            r"do not|must not|never",
            r"expand|extrapolat|add|imply",
        )

    def test_safe_01_replacement_preserves_evidence_quantities_exactly(self):
        self.assert_rule(
            "resume-and-cover-letter",
            r"replacement wording",
            r"number|quantity|duration|metric",
            r"exact",
            r"do not|must not|never",
            r"over|more than|at least|approximately|round",
        )

    def test_safe_02_stop_before_submit_uses_approved_current_state(self):
        self.assert_rule(
            "assisted-application",
            r"STOP BEFORE FINAL SUBMIT",
            r"preserve|keep|remain",
            r"pre[- ]submit|existing (?:application_)?stage|current (?:application_)?stage",
            r"action_status",
            r"candidate_review",
            r"blocked",
            r"next_action",
            r"candidate.{0,40}confirm",
            r"actual|successful",
            r"only.{0,30}advance|advance.{0,30}only",
        )

    def test_safe_02_handoff_requests_review_then_candidate_submit(self):
        self.assert_rule(
            "assisted-application",
            r"STOP BEFORE FINAL SUBMIT",
            r"ask.{0,30}candidate.{0,30}(?:perform|complete).{0,20}final review",
            r"preserve",
            r"pre[- ]submit|existing (?:application_)?stage",
            r"then.{0,20}ask.{0,30}candidate.{0,30}submit personally",
        )

    def test_safe_02_pause_does_not_invent_a_persistence_identifier(self):
        self.assert_rule(
            "assisted-application",
            r"workflow pause",
            r"status_changed",
            r"only when|when.{0,30}actually changes",
            r"do not|must not|never",
            r"invent",
            r"event|persistence identifier",
        )

    def test_ranking_scores_require_documented_factor_evidence(self):
        self.assert_rule(
            "job-screening-and-scoring",
            r"numeric|score|subscore",
            r"factor",
            r"documented|available|supplied",
            r"evidence",
            r"unavailable|omit|do not|must not|never",
            r"invent|fabricat|infer|guess|calculate",
        )

    def test_ranking_decision_appends_history_event(self):
        self.assert_rule(
            "job-screening-and-scoring",
            r"ranking|shortlist",
            r"append",
            r"event",
            r"canonical_job_id",
            r"jd_snapshot_path",
        )

    def test_screening_decision_is_persisted_before_it_is_reported(self):
        self.assert_ordered_rule(
            "job-screening-and-scoring",
            r"screening|ranking|shortlist|exclusion",
            r"current[- ]state",
            r"append",
            r"event",
            r"before",
            r"report",
        )
        self.assert_ordered_rule(
            "assisted-application",
            r"STOP BEFORE FINAL SUBMIT",
            r"ask.{0,30}candidate.{0,30}(?:perform|complete).{0,20}final review",
            r"then.{0,20}ask.{0,30}candidate.{0,30}submit personally",
        )

    def test_safe_04_unauthorised_analytics_define_only_safe_future_sharing(self):
        self.assert_rule(
            "privacy-and-consent",
            r"not authori[sz]ed|without authori[sz]ation|no authori[sz]ation",
            r"future.{0,30}authori[sz]ed|if.{0,30}authori[sz]ed",
            r"anonymous aggregate",
            r"only",
            r"exclude|never include|must not include",
            r"identifiable (?:candidate )?data|candidate-identifiable data",
        )

    def test_safe_05_no_change_schedule_is_quiet_and_outside_tracker_events(self):
        self.assert_rule(
            "privacy-and-consent",
            r"authori[sz]ed (?:scheduled )?run",
            r"no actionable change",
            r"automation (?:execution|audit)",
            r"run[- ]level",
            r"Top N",
            r"unchanged|do not change|preserve",
            r"per[- ]candidate|discovery outcomes",
            r"outside",
            r"tracker event vocabulary",
            r"do not invent",
            r"quiet|silent",
        )

    def test_safe_06_decision_record_links_exact_snapshot_provenance_or_fails_closed(self):
        self.assert_rule(
            "job-screening-and-scoring",
            r"decision record",
            r"canonical_job_id",
            r"exact.{0,20}jd_snapshot_path|jd_snapshot_path.{0,20}exact",
            r"source URL",
            r"retrieval time",
            r"before.{0,40}(?:shortlist|preparation)",
            r"missing|incomplete",
            r"fail closed|must not|do not|cannot",
        )

    def test_shortlist_output_traces_each_role_through_both_gates_and_snapshot(self):
        section = reference("job-screening-and-scoring").split(
            "## Shortlist output", 1
        )[1]
        patterns = (
            r"each shortlisted (?:job|role)",
            r"Verification verdict",
            r"Hard Gate verdict",
            r"dated (?:JD )?snapshot",
            r"retrieval time",
            r"verified link",
            r"canonical_job_id",
            r"jd_snapshot_path",
            r"unavailable|missing",
        )
        self.assertTrue(
            any(all(re.search(pattern, block, re.I) for pattern in patterns)
                for block in blocks(section)),
            "Shortlist output must trace every role through both gates and its dated JD snapshot",
        )


if __name__ == "__main__":
    unittest.main()
