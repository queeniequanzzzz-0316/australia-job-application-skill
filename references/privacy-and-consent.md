# Privacy and consent

## Local boundary

Candidate information and personal analytics stay local by default in the private workspace. Public examples must be synthetic only. Never commit or share real PII, credentials, private documents, workspace artifacts, private absolute paths or raw application answers in the public repository or public examples. Do not treat removal of a name as sufficient anonymisation.

Keep employer-facing information limited to the candidate-approved application and authorised form assistance. Publication, analytics sharing and scheduling are separate decisions; preparing an application does not authorise any of them.

## Separate consent boundaries

Cross-user analytics is disabled by default and requires separate explicit opt-in. Anonymous aggregate analytics also requires that separate explicit opt-in before any sharing; show the proposed data and destination for approval. Never export or share identifiable data through analytics, including resumes, free text, contact details or raw answers.

When analytics sharing is not authorised, explicitly state that any future authorised sharing is anonymous aggregate only and must exclude identifiable candidate data.

Salary-band sharing requires its own separate consent in addition to analytics consent. Broad work-rights category sharing requires separate consent for that dimension; visa-related categories may be shared only within the separately approved boundary. Exact salary and visa identifiers remain local. Do not infer these permissions from general analytics opt-in, an application answer or one another.

No omnibus or bundled consent: record which purpose, data and action the candidate approved. Honour withdrawal for future sharing and scheduling. No cross-user backend or hosted service is provided; do not invent an upload destination, collection service or automatic export capability.

## Optional schedules

No automatic scheduling or enabling of workflows. Offer optional schedules after onboarding; leave them disabled unless the candidate explicitly authorises the individual workflow and schedule. Automation consent is separate from analytics consent and does not imply data sharing or Assisted Apply permission.

- Morning Discovery requires its own separate explicit authorisation. Use only the latest confirmed profile and filters; verify, deduplicate and screen under [references/job-screening-and-scoring.md](job-screening-and-scoring.md). Pause if required confirmation is missing. Do not prepare or submit applications automatically.
- Evening Check-in requires its own separate explicit authorisation. Review known applications, candidate updates, approvals, blockers and follow-ups under [references/tracker-and-analytics.md](tracker-and-analytics.md). Evening Check-in does no new discovery.

For an authorised scheduled run with no actionable change, keep Top N unchanged and remain quiet: produce no user-visible commentary, progress, status or final message. Keep the run-level outcome and per-candidate discovery outcomes, when an automation audit is required, in an automation execution record outside `tracker/applications.csv`, `tracker/events.jsonl` and the Task 6 tracker event vocabulary. Do not invent a tracker event type for the run, and do not mutate an application record when no application state changed. Notify only on a meaningful change or required candidate action. A scheduled run never relaxes confirmation, privacy or final-submit boundaries.
