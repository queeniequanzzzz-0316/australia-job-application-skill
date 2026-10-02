# Resume and cover letter

## Preparation flow

1. The candidate selects a verified job before preparation begins. Require the screening decision record that links its `canonical_job_id`, exact `jd_snapshot_path`, source URL and retrieval time; if any linkage is missing or incomplete, fail closed and do not begin preparation. Resolve changed job evidence before drafting.
2. Preserve the Master Resume and create a tailored, job-specific document version. Tailor ordering, emphasis and language using only supported evidence for each claim; do not change the underlying facts. Apply the same rule to cover letters and application answers.
3. Perform an Evidence Audit: map each material claim to its source evidence and the relevant JD requirement, check consistency of dates, names, skills and numbers, and record gaps. Use the exact provenance taxonomy in [references/job-screening-and-scoring.md](job-screening-and-scoring.md).
4. Remove unsupported claims from submission-ready documents or surface them as `needs_confirmation` in the review draft. When refusing an unsupported claim, immediately provide actual truthful replacement wording grounded in available evidence in the same response; do not merely promise a later replacement. Replacement wording may use only the exact verified or supported facts: never add, expand, extrapolate or imply activities, responsibility, scope or outcomes that the evidence does not establish. Preserve every supported number, quantity, duration and metric exactly; do not change an exact value into “over,” “more than,” “at least,” “approximately,” or a rounded value. Ask for the underlying fact and supporting context before incorporating a correction.
5. Present material changes, remaining gaps and the audited documents for candidate approval. Candidate approval of wording does not validate an unsupported fact; confirmation must establish the underlying claim. Mark documents ready only when the audit and approval are complete.

## Truthfulness boundary

Never fabricate or inflate metrics, years of experience, tools, responsibilities, work rights, salary or declarations. Never guess unknown declarations. Preserve the original scope and strength of evidence; a job requirement does not supply missing candidate experience.

Hard-requirement gaps must remain gaps, not be disguised as transferable experience. Describe supported adjacent experience accurately without claiming it satisfies a mandatory qualification or capability it does not establish.

## Traceability

Record the exact `canonical_job_id`, `jd_snapshot_path` and document version for every tailored output. Maintain the claim-to-evidence links in the private workspace and record approved `document_versions` through the tracker's document update event. Do not replace a referenced snapshot or silently overwrite earlier document versions; a changed JD requires an explicit link to the version used.

Use [references/tracker-and-analytics.md](tracker-and-analytics.md) for event history and [references/assisted-application.md](assisted-application.md) only when the candidate explicitly authorises form assistance.
