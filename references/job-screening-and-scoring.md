# Job screening and scoring

Use the latest confirmed profile and filters. The required order is **Discovery -> Verification -> Hard Gate -> Top N**.

## Gates

1. Discovery: inspect search-result evidence first. Assign or reuse `canonical_job_id` for cross-platform deduplication; compare employer, role, location and application identity. After deterministic deduplication, exactly one canonical opportunity enters the ranking input candidate set and is counted once; never count source copies separately. This inclusion is not Top N admission or a fit verdict: Verification and Hard Gate still control Top N eligibility. If candidate fit evidence is unavailable, record candidate fit as unknown and do not fabricate, infer or guess it.
2. Verification: retrieve and verify the full JD and live application entry for plausible roles before expensive fit analysis. Confirm employer, location, requirements and legitimacy. Before shortlist or preparation, the decision record must link `canonical_job_id`, the exact `jd_snapshot_path`, source URL and retrieval time. If any linkage is missing or incomplete, fail closed: do not shortlist or prepare the application. Every Top N shortlist entry requires a dated snapshot with source URL and retrieval time; retain the exact `jd_snapshot_path` returned by the snapshot tool. Keep failed or incomplete verification outside Top N.
3. Hard Gate: record confirmed blockers involving work rights, location, sponsorship, mandatory requirements, closed entries, duplicates or scam indicators. Unresolved essential eligibility remains pending verification. Update `screening_status` with the reason and supporting evidence; do not label unknown eligibility as eligible.
4. Top N: score and rank only after the preceding gates. Top N is an upper bound; never pad it with unsuitable or unverified roles. Explain a shortfall. Only the eligible verdicts below may enter it, and those verdicts do not waive any gate.

For every screening, ranking, shortlist or exclusion decision, first update the canonical job's current-state tracker row and append the corresponding history event with its reason and supporting evidence before reporting the decision. Reporting a decision never substitutes for either persistence operation.

## Professional defaults

| Factor | Weight |
| --- | --- |
| Core capability match | 25% |
| Evidence in candidate profile or resume | 20% |
| Responsibility and seniority fit | 15% |
| Work rights and location | 15% |
| Industry transferability | 10% |
| Employment preferences | 5% |
| Application competitiveness | 5% |
| Career value | 5% |

Default weights sum to 100. Candidate-defined weights override defaults; record the confirmed replacement weights and keep the total at 100. Explain each factor's assessment using evidence. A hard blocker cannot be offset by a high score. Scores are ranking aids, not interview probabilities. Use [references/casual-and-part-time.md](casual-and-part-time.md) for that stream's weights.

Calculate and report a numeric score or factor subscore only when the supplied record contains enough documented evidence for every factor used in that calculation. Never invent, infer or guess a factor assessment to complete a score. When factor-level evidence is unavailable, omit the numeric score or label it unavailable and rank only by the supported gate and evidence record.

| Verdict | Top N eligible | Meaning |
| --- | --- | --- |
| `apply_first` | yes | Strong supported fit and ready to prioritise. |
| `apply_with_targeted_edits` | yes | Supported fit requiring specific document changes. |
| `stretch` | yes | Plausible fit with clearly disclosed non-blocking gaps. |
| `do_not_apply` | no | Blocked, unsuitable or insufficiently verified. |

## Evidence Audit provenance

Use exactly these source classifications; provenance identifies origin, not truth or confidence.

| Provenance | Meaning |
| --- | --- |
| `candidate_statement` | A fact explicitly supplied or confirmed by the candidate. |
| `candidate_document` | A claim extracted from a candidate-supplied document. |
| `third_party_document` | Information in an external document that has not been independently verified. |
| `verified_third_party` | External information checked against a recorded authoritative source. |

Keep source, claim, verification date where relevant, and confidence alongside the classification. Do not promote a document claim to independent verification without a check. Missing support remains an unknown fact or gap, not an additional provenance category. Apply the claim-level Evidence Audit in [references/resume-and-cover-letter.md](resume-and-cover-letter.md) to material screening decisions as well as application content.

## Shortlist output

For each shortlisted job, show the ranking verdict, explicit Verification verdict, explicit Hard Gate verdict, score, fit evidence, main gaps, hard blockers, missing information and evidence confidence. Trace every role to its dated JD snapshot by showing the retrieval time, verified link, `canonical_job_id` and `jd_snapshot_path`; use the provided identifiers and evidence, and label any unavailable field as unavailable rather than inventing it. Append each ranking or shortlist decision to the event log with its `canonical_job_id` and exact `jd_snapshot_path`; reporting a decision does not replace recording its history event.

Give separate, independent HR and Hiring Manager views grounded in the same profile evidence and JD snapshot. HR assesses eligibility and the clarity of the application; Hiring Manager assesses capability to perform the work. Preserve disagreement and uncertainty. End with targeted edits and the next action; application preparation starts only after candidate selection.
