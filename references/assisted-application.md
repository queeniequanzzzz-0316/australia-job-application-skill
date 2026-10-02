# Assisted Apply

## One-job procedure

1. The candidate chooses one verified job. Obtain explicit job-specific authorisation for that one job before opening and assisting with its application. General search or document approval is not form-assistance authorisation.
2. Recheck the job and application entry. Use approved documents linked to `canonical_job_id` and `jd_snapshot_path`; fill ordinary fields only with current candidate-confirmed information.
3. Pause for unknown, sensitive, legal or declaration questions. Surface the exact question and request candidate input or direct candidate completion. Never guess declarations or infer answers from omissions. Record the unresolved next action without claiming submission.
4. Present a final review of the job, documents, entered answers and unresolved issues. Keep `action_status` at `candidate_review` or `blocked` as appropriate; filling a form does not advance `application_stage` to `submitted`.
5. **STOP BEFORE FINAL SUBMIT**. Explicitly ask the candidate to perform the final review, then ask the candidate to submit personally; authorisation to assist never includes permission for the agent to perform this action. Preserve the existing `application_stage` and pre-submit state. Represent the workflow pause with the approved current-state fields: keep `action_status` at `candidate_review`, or `blocked` when candidate input is unresolved, and set `next_action` to the specific candidate review or submission step. Append a `status_changed` event only when one of those approved current-state fields actually changes. If no tracked field changes, do not invent an event or persistence identifier merely to label the pause. Only advance `application_stage` after candidate confirmation of actual successful submission.
6. Ask the candidate to confirm actual successful submission. Record `application_stage` as `submitted` only after the candidate confirms actual submission, with the supported timing and linked document versions. Until then, preserve the existing stage and identify what remains outstanding.

## Prohibitions

Never perform automatic, bulk or silent submission. Do not reinterpret a final-send control as an ordinary field or use another route to submit. Never guess unknown declarations, work-rights answers or other candidate facts.

Follow [references/resume-and-cover-letter.md](resume-and-cover-letter.md) for the Evidence Audit, [references/tracker-and-analytics.md](tracker-and-analytics.md) for recording changes and [references/privacy-and-consent.md](privacy-and-consent.md) for sensitive boundaries.
