# Job screening and scoring

Use the latest confirmed profile and filters. The required order is **Discovery -> Verification -> Hard Gate -> Top N**.

## Gates

1. Discovery: inspect search-result evidence first. Assign or reuse `canonical_job_id` for cross-platform deduplication; compare employer, role, location and application identity. Preserve source links and resolve uncertain duplicates rather than counting them twice.
2. Verification: retrieve and verify the full JD and live application entry for plausible roles before expensive fit analysis. Confirm employer, location, requirements and legitimacy. Every Top N shortlist entry requires a dated snapshot with source URL and retrieval time; retain the exact `jd_snapshot_path` returned by the snapshot tool. Keep failed or incomplete verification outside Top N.
3. Hard Gate: record confirmed blockers involving work rights, location, sponsorship, mandatory requirements, closed entries, duplicates or scam indicators. Unresolved essential eligibility remains pending verification. Update `screening_status` with the reason and supporting evidence; do not label unknown eligibility as eligible.
4. Top N: score and rank only after the preceding gates. Top N is an upper bound; never pad it with unsuitable or unverified roles. Explain a shortfall. Only the eligible verdicts below may enter it, and those verdicts do not waive any gate.

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

For each shortlisted job, show verdict, score, fit evidence, main gaps, hard blockers, missing information and evidence confidence, together with the verified link, `canonical_job_id` and `jd_snapshot_path`.

Give separate, independent HR and Hiring Manager views grounded in the same profile evidence and JD snapshot. HR assesses eligibility and the clarity of the application; Hiring Manager assesses capability to perform the work. Preserve disagreement and uncertainty. End with targeted edits and the next action; application preparation starts only after candidate selection.
