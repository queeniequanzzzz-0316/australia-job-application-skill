# Search filters

Use after profile confirmation. Map one candidate-approved filter to SEEK, LinkedIn and Indeed; platform settings are translations of that filter, not independent permission to broaden it.

## Confirm the filter

Show role titles and exclusions, locations and commute limits, employment types, work arrangement, pay basis and bounds, recency, sponsorship need and hard gates. Preserve unknown preferences as unknown. The candidate can edit and configure every preference and Top N. Explain restrictive combinations and ask for changes rather than silently relaxing them. Confirm filters before Discovery and renew confirmation after material changes.

Follow **Discovery -> Verification -> Hard Gate -> Top N**. Complete verification before expensive fit analysis. Admit a job to Top N only after all preceding gates pass.

Top N is an upper bound, not a quota; never pad results. Screening and cross-platform deduplication by `canonical_job_id` belong to [references/job-screening-and-scoring.md](job-screening-and-scoring.md).

## Runtime platform mapping

Verify current platform controls and filter behaviour at runtime. No frozen policy assumptions or hardcoded controls: if a native filter is missing, use a focused query and verify the requirement in the job text. Report unsupported mappings and residual uncertainty.

| Platform | Mapping procedure | Authoritative check |
| --- | --- | --- |
| SEEK | Map confirmed role, location, work type, pay and recency to available controls; verify remaining constraints in the JD. | Consult current SEEK Australia guidance and live controls at runtime. |
| LinkedIn | Map role, location, work arrangement and employment type where supported; verify the full JD and application entry. | Consult current LinkedIn Help and check live controls at runtime. |
| Indeed | Map role, location, employment type, pay and recency where available; treat sponsorship words as signals needing verification. | Consult current Indeed Australia Help and check live controls at runtime. |

Record the actual query, source and limitations with discovery results. Remote wording does not establish permission to work from any country or location; check the role's restrictions against the confirmed profile. All searches remain Australia-only.
