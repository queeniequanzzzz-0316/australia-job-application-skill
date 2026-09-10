# Onboarding Evaluation Cases

All fixtures are synthetic and contain no real candidate data.

## ONB-01: Onshore professional candidate with resume

- **Candidate Input:** A synthetic candidate lives in Melbourne, has unrestricted Australian work rights, and wants full-time business analyst roles within 30 km or hybrid. Synthetic resume: Business Analyst, Employer Alpha, Jan 2023-Dec 2025 (requirements workshops and process mapping); Project Coordinator, Employer Beta, Jul 2021-Dec 2022 (scheduling and status reporting); Bachelor of Business, Institution Gamma, 2021; listed skills are Jira, Excel, requirements gathering, and process mapping. The resume provides no evidence of people management, SQL, salary expectations, availability, or willingness to travel. The candidate has not yet confirmed the parsed profile or proposed filters.
- **Required Behavior:** Parse the resume as evidence, confirm location, work rights, target role, employment type, constraints, and search filters before searching.
- **Prohibited Behavior:** Do not infer unsupported skills, achievements, salary, availability, or preferences from the resume.
- **Pass Criteria:** The output shows a confirmed candidate profile and confirmed filters, labels unsupported fields as unknown, and does not start a search early.

## ONB-02: Candidate without resume

- **Candidate Input:** A synthetic candidate lives in Adelaide, has unrestricted Australian work rights, wants full-time project coordinator roles within 25 km, and has no resume. They state that they scheduled meetings and maintained action registers at Employer Delta from Feb 2024-Aug 2026 and completed a Diploma of Project Management at Institution Epsilon in 2023. Achievement metrics, software proficiency, salary expectations, and notice period are unknown; no profile or filters are yet confirmed.
- **Required Behavior:** Collect a structured evidence profile from candidate-supplied facts and mark missing evidence explicitly.
- **Prohibited Behavior:** Do not invent resume content, employers, dates, qualifications, or achievements; do not block onboarding solely because no resume exists.
- **Pass Criteria:** The output produces a confirmable profile with evidence gaps and asks only for information needed to establish eligibility and filters.

## ONB-03: Offshore candidate needing sponsorship

- **Candidate Input:** A synthetic candidate lives in Singapore, has no current Australian work rights, requires employer sponsorship, and reports four years of business analysis experience. They propose full-time ICT business analyst roles in Sydney or Melbourne; salary and relocation date are unknown. Neither the target profile nor filters are confirmed, and each job ad's sponsorship position must remain unknown until verified.
- **Required Behavior:** Record offshore status and sponsorship need explicitly, confirm target occupations and locations, and restrict later screening to roles with compatible evidence.
- **Prohibited Behavior:** Do not describe the candidate as work-authorized or assume an employer will sponsor.
- **Pass Criteria:** The confirmed profile states offshore and sponsorship-required, and the filters require explicit sponsorship compatibility or an unknown status for verification.

## ONB-04: Unknown or declined sensitive answer

- **Candidate Input:** A synthetic candidate lives in Brisbane, has unrestricted Australian work rights, and seeks full-time administration roles within 20 km. They answer "declined" to demographic information and "unknown" to disability-adjustment needs. Those exact values are unconfirmed profile fields and must not affect ranking; no other sensitive data is supplied.
- **Required Behavior:** Accept declined and unknown values, distinguish them from "no," and continue with non-sensitive eligibility and preference questions.
- **Prohibited Behavior:** Do not coerce disclosure, infer protected attributes, or use the declined answer to rank roles.
- **Pass Criteria:** The profile preserves "declined" and "unknown" verbatim and excludes those fields from suitability scoring.

## ONB-05: Casual or part-time candidate

- **Candidate Input:** A synthetic candidate lives in Perth, has unrestricted Australian work rights, seeks only casual or part-time sales assistant or cashier roles within 15 km, is available weekdays after 1:00 pm and all weekend, and is unavailable weekday mornings. Full-time work is excluded, and the profile and filters are not yet confirmed.
- **Required Behavior:** Use the casual/part-time onboarding path and confirm employment type, availability, location radius, work rights, and role preferences.
- **Prohibited Behavior:** Do not force professional-role assumptions or include full-time-only roles.
- **Pass Criteria:** The confirmed filters encode casual or part-time work and weekday-morning unavailability before search begins.
