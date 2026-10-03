# Applying for Jobs in Australia

This Codex skill provides evidence-based guidance for Australian job discovery, application preparation, and job-specific form-filling assistance. It supports onshore and offshore candidates seeking professional, full-time, casual, or part-time work.

The workflow confirms candidate facts and search filters before discovery, verifies job descriptions before ranking, applies hard gates before fit analysis, and separates HR and Hiring Manager perspectives. It does not invent candidate experience, work rights, salary answers, or application declarations. Assisted Apply stops before the final Submit action; the candidate reviews and submits every application.

## Version 0.1.0

Version 0.1.0 includes:

- Australia-only onboarding for onshore and offshore candidates.
- Professional and casual/part-time search and screening workflows.
- Confirmed candidate profiles and customizable search filters.
- JD verification, hard gates, canonical-job deduplication, and Top N ranking without padding.
- Evidence-audited resume and cover-letter preparation.
- A private local workspace with JD snapshots that become immutable once referenced, current-state application tracking, and an append-only event log.
- Local analytics and an on-demand Excel dashboard with `Overview`, `Applications`, `Conversion`, and `Follow-ups` sheets.
- Explicit consent boundaries for scheduled workflows and anonymous analytics.
- Behavioral evaluation and repository privacy gates.

See [SKILL.md](SKILL.md) for orchestration and the [workflow references](references/) for detailed contracts. Release history is recorded in [CHANGELOG.md](CHANGELOG.md).

## Private Workspace

Candidate facts, resumes, cover letters, application answers, job records, and analytics belong in a separate local workspace. Do not create that workspace inside this public repository.

Install the Python dependencies, then initialize a workspace outside the repository:

```bash
python3 -m pip install -r requirements.txt
python3 scripts/initialise_workspace.py \
  --destination /path/to/private-workspace
```

The initializer creates the approved directories and conservative templates. Scheduled workflows and cross-user analytics remain disabled until the candidate explicitly authorizes them.

## Local Tools

The repository provides command-line tools for the validated local workflow:

```text
scripts/validate_profile.py
scripts/save_job_snapshot.py
scripts/update_tracker.py
scripts/build_analytics_summary.py
scripts/build_dashboard.py
scripts/check_repository_privacy.py
```

Operational data remains in the private workspace. `applications.csv`, `events.jsonl`, and the validated analytics summary are the source records; `dashboard.xlsx` is a disposable presentation layer.

## Safety

- Australia-only scope.
- Candidate evidence is never fabricated or silently inferred.
- Unverified jobs and hard-gate failures do not enter recommendations.
- Job-specific documents remain linked to the exact `canonical_job_id` and JD snapshot.
- Assisted Apply handles one selected job at a time and stops before final submission.
- Anonymous cross-user analytics and morning/evening schedules require separate explicit authorization.
- The repository privacy scanner reports only file, line, and finding category; it does not echo detected secrets or personal data.

Licensed under the [MIT License](LICENSE).
