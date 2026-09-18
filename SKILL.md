---
name: applying-for-jobs-in-australia
description: Use for evidence-based Australian job discovery, resume and cover-letter preparation, assisted applications, and consent-based job-search tracking for onshore or offshore candidates across professional and casual or part-time work.
---

# Applying for Jobs in Australia

## Scope and Safety

Operate only in the Australian job market. Support onshore and offshore candidates across professional and casual or part-time work streams.

Never fabricate, inflate, or silently infer candidate facts, evidence, work rights, qualifications, experience, metrics, or application answers. Apply an Evidence Audit to every material candidate claim and screening decision: link it to confirmed evidence or mark it unsupported, unknown, or needing confirmation.

Candidate data belongs in the private local workspace and must never be committed to the public skill repository.

## Required Confirmations

Confirm the candidate profile before using it. Then confirm the candidate-controlled search filter, including role, location, work arrangement, eligibility constraints, and Top N. Scheduled searches may use only the latest confirmed profile and filter.

## Operating Modes

- **Job Discovery** is the default. Find, verify, deduplicate, screen, and rank roles without preparing every application.
- **Application Preparation** begins only after the candidate selects a role. Analyse the full JD and prepare evidence-grounded resume, cover-letter, and application-answer material.
- **Assisted Apply** requires explicit, job-specific authorization and handles one role at a time. Help with ordinary form fields, pause on unknown or sensitive answers, present a final review, and always stop before the final **Submit** action. The candidate performs submission.

## Orchestration Gates

Move roles through **Discovery -> Verification -> Hard Gate -> Top N**. Use progressive JD retrieval: inspect lower-cost discovery evidence first, then retrieve and verify the complete JD only for plausible candidates. Do not place a role in Top N or begin Application Preparation until a dated JD snapshot records the source URL and retrieval time.

For every shortlisted role, provide separate **HR** and **Hiring Manager** perspectives grounded in the same candidate evidence and JD snapshot. Do not pad Top N when fewer roles pass the gates.

Maintain both the tracker's current state and an append-only event log. Preserve traceability from each application, generated document version, and event to the exact dated JD snapshot used; never overwrite history.

Morning discovery and evening check-in automation each require separate explicit consent. Anonymous cross-user analytics requires another separate explicit opt-in; never infer any consent. Authorized scheduled runs with no actionable change update local records but remain quiet.

## Reference Routing Boundary

`SKILL.md` owns mode selection, orchestration, safety, consent, and gate rules. The following references provide the detailed procedures; read only the one needed for the current workflow and follow its links when related guidance is required.

| Reference | Detailed ownership |
| --- | --- |
| `references/onboarding.md` | Candidate interview, profile fields, resume intake, and profile confirmation |
| `references/australian-work-rights.md` | Current official-source checks, onshore/offshore distinctions, sponsorship, and legal-advice limits |
| `references/search-filters.md` | Candidate-editable filters and live platform mapping |
| `references/job-screening-and-scoring.md` | Discovery, verification, deduplication, hard gates, ranking, progressive retrieval, and HR/Hiring Manager outputs |
| `references/resume-and-cover-letter.md` | Evidence-grounded tailoring, Evidence Audit, and document-to-JD linkage |
| `references/casual-and-part-time.md` | Availability, rates, awards, physical requirements, and role-specific licences |
| `references/assisted-application.md` | Job-specific form assistance, candidate review, safeguards, and the no-submit boundary |
| `references/tracker-and-analytics.md` | Current-state tracking, append-only events, snapshots, document traceability, and personal analytics |
| `references/privacy-and-consent.md` | Local data boundaries, sensitive data, automation consent, and anonymous analytics consent |
