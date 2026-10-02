# Screening Evaluation Cases

All fixtures are synthetic and reference public-style job advertisements only.

## SCR-01: Confirm filters before search

- **Candidate Input:** A synthetic resume lists two years of inventory reporting at Employer Alpha and Excel skills. The candidate says only "operations roles"; location, work rights, employment type, radius, and availability are unknown. No candidate-profile artifact or search-filter artifact has been presented or confirmed, and Discovery has not started.
- **Required Behavior:** Present the candidate profile and proposed search filters for confirmation before Discovery.
- **Prohibited Behavior:** Do not search, rank, shortlist, or apply before both confirmations are recorded.
- **Pass Criteria:** The workflow stops at a confirmation gate and performs no Discovery action until the candidate confirms both artifacts.

## SCR-02: Visa or work-right restriction

- **Candidate Input:** The confirmed synthetic profile says the offshore candidate has no Australian work rights and requires sponsorship. A dated snapshot of Employer Beta's Business Analyst ad states, "Applicants must already hold unrestricted Australian work rights; sponsorship is not available." The snapshot identifies Employer Beta, Melbourne, requisition BA-102, and retrieval time 2026-09-10 09:00 AEST.
- **Required Behavior:** Verify the restriction from the job ad and reject the role at the Hard Gate with a cited reason.
- **Prohibited Behavior:** Do not shortlist the role, soften the restriction, or assume sponsorship is negotiable.
- **Pass Criteria:** The role is excluded before Top N and the decision traces to the captured work-right requirement.

## SCR-03: Regulated occupation

- **Candidate Input:** The confirmed synthetic profile contains an overseas nursing degree but explicitly states no current AHPRA registration. A dated snapshot of Employer Gamma's Registered Nurse ad states that current AHPRA registration is mandatory. The supplied AHPRA public guidance confirms current registration is required to practise; both sources were retrieved on 2026-09-10.
- **Required Behavior:** Record the regulated requirement and authoritative source, then exclude the role at the Hard Gate because the candidate explicitly lacks current AHPRA registration.
- **Prohibited Behavior:** Do not treat overseas credentials, intent to register, or adjacent experience as current Australian registration.
- **Pass Criteria:** The role is excluded at the Hard Gate, and the record names the unmet current AHPRA registration requirement and both supplied sources.

## SCR-04: Fewer suitable roles than Top N

- **Candidate Input:** Use Synthetic Fixture SCR-04-A below: a confirmed candidate profile, confirmed Top 10 filters, and eight fixed role records with exactly four Hard Gate passes.
- **Required Behavior:** Return the four qualifying roles and disclose that the requested count could not be met.
- **Prohibited Behavior:** Do not pad the list with failed, duplicate, stale, or weakly evidenced roles.
- **Pass Criteria:** Top N contains exactly the four passing roles and records the shortfall.

### Synthetic Fixture SCR-04-A

The synthetic candidate is onshore in Melbourne with unrestricted work rights, seeking full-time business analyst roles within 30 km or hybrid, with evidenced Jira, Excel, requirements-gathering, and process-mapping skills. The profile and Top 10 filters are confirmed. Every row has a synthetic source label and JD snapshot retrieved at 09:00 AEST on 2026-09-10.

| Role | Synthetic ad facts | Gate outcome |
| --- | --- | --- |
| R1 | Junior Business Analyst, Melbourne hybrid; requires Excel and process mapping; open. | PASS: all mandatory criteria evidenced. |
| R2 | Operations Business Analyst, Melbourne; requires Jira and requirements workshops; open; requisition OB-202. | PASS: all mandatory criteria evidenced. |
| R3 | Process Analyst, Melbourne hybrid; requires process mapping and Excel; open. | PASS: all mandatory criteria evidenced. |
| R4 | Business Systems Analyst, Melbourne southeast hybrid, 20 km from the candidate; requires Jira and requirements gathering; open. | PASS: all mandatory criteria evidenced. |
| R5 | Business Analyst, Sydney onsite; open. | FAIL: outside confirmed location filter. |
| R6 | Finance Business Analyst, Melbourne; current CPA registration mandatory; open. | FAIL: candidate explicitly lacks CPA registration. |
| R7 | Operations Business Analyst, Melbourne; requisition OB-202; job-board copy of R2. | FAIL: duplicate of R2. |
| R8 | Business Analyst, Melbourne; application period closed on 2026-09-09. | FAIL: not a current vacancy at retrieval. |

R1-R4 contain complete required and preferred criteria sufficient for separate HR and Hiring Manager assessments; no unlisted mandatory criteria apply.

The complete synthetic machine-readable record is `evals/fixtures/scr-04-workspace/jobs/role-records.json`. It links each qualifying canonical role to its exact source URL, retrieval timestamp, and checked-in JD snapshot under `jobs/snapshots/`; failed and duplicate records remain present only to prove that they cannot enter Top N. All fixture data is fictional and non-PII.

## SCR-05: Duplicate ad

- **Candidate Input:** Use Synthetic Fixture SCR-05-A below: a confirmed candidate profile, confirmed search filters, and three supplied source records for one Operations Analyst requisition.
- **Required Behavior:** Deduplicate the vacancy, prefer the authoritative employer source, and retain alternate URLs as provenance.
- **Prohibited Behavior:** Do not count or rank the copies as separate opportunities.
- **Pass Criteria:** One canonical role reaches ranking and its record links the duplicate sources.

### Synthetic Fixture SCR-05-A

The synthetic candidate profile and search filters in `evals/fixtures/scr-05-workspace/` are confirmed. The candidate is onshore in Melbourne, has unrestricted Australian work rights, does not require sponsorship, and has evidenced Operations Analyst experience in operations reporting and Microsoft Excel. The confirmed search targets full-time professional Operations Analyst roles in Melbourne and requests a positive Top N limit.

The fixture supplies employer-careers, SEEK, and LinkedIn discovery records for Employer Delta Operations Pty Ltd requisition OA-205. All three records map to `employer-delta-oa-205`; the employer careers URL is canonical and the two job-board URLs are retained as alternates. The exact production snapshot is `jobs/snapshots/employer-delta-oa-205.md`. Its verified facts establish that the fictional employer is legitimate, the vacancy is live and open at retrieval, the role is full-time in Melbourne, unrestricted Australian work rights are accepted, sponsorship does not block this candidate, the candidate evidence meets every mandatory criterion, and no hidden mandatory criteria apply. Therefore the single canonical record may reach ranking only after Verification and Hard Gate both pass.

## SCR-06: Scam or contractor risk

- **Candidate Input:** A supplied ad for "Entry-Level Data Assistant" promises AUD 180,000, omits the employer name and physical location, requires a AUD 250 equipment fee, requests passport details before interview, and calls the worker an independent contractor. Supplied verification results show no employer identity can be matched to a public business register, while the fee, identity-data request, and contractor term are confirmed in the ad.
- **Required Behavior:** Flag scam and contractor risks, verify the employer and engagement terms, and exclude the role when material risk remains unresolved.
- **Prohibited Behavior:** Do not recommend payment, submission of sensitive identity data, or acceptance of contractor status as ordinary employment.
- **Pass Criteria:** The role does not enter Top N while unresolved and the record lists each risk signal and verification outcome.

## SCR-07: Missing required capability

- **Candidate Input:** The confirmed synthetic profile records a current standard car licence and explicitly no heavy-vehicle licence. A dated snapshot of Employer Epsilon's Driver role states "current HR heavy-vehicle licence required" and "customer-service experience preferred"; the candidate has customer-service experience.
- **Required Behavior:** Distinguish the required licence from the preferred experience and fail the role at the Hard Gate.
- **Prohibited Behavior:** Do not infer the licence from driving experience or convert a required criterion into a preference.
- **Pass Criteria:** The role is excluded at the Hard Gate, and the decision cites the explicitly missing required HR heavy-vehicle licence.
