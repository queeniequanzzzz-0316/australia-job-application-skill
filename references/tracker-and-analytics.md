# Tracker and analytics

## Record evidence and state

`tracker/applications.csv` holds current state, with one row per `canonical_job_id`. `tracker/events.jsonl` is the append-only source of truth for history; preserve events and never silently rewrite or delete them. Use the existing tracker tools and their schemas. If state and history disagree, report the inconsistency rather than invent or migrate records.

Keep `screening_status`, `application_stage` and `action_status` separate: suitability, application progress and the next operational action are distinct. Preserve the existing `discovery_status` and `verification_status` dimensions as well.

For every snapshot, application, document version and event, retain the same `canonical_job_id` and the exact `jd_snapshot_path` used. Record `document_versions` in document update events. A snapshot referenced by the tracker remains immutable; changed content uses a new snapshot path, leaving earlier links intact.

Record `next_action`, `action_due_at` and `blocker_category` explicitly. Unknown dates stay empty; use timezone-aware timestamps when timing is known. Submission follows candidate confirmation under [references/assisted-application.md](assisted-application.md).

Count an employer response only from explicit `first_response_at` evidence in the current tracker row. Never infer a response from interview, offer, rejected, withdrawn or accepted lifecycle states, or use these as response proxies. Event history does not supply missing response timestamps.

## Conversion formulas

Every metric is the stated numerator divided by its denominator. Deduplicate all analytics counts by `canonical_job_id` across platforms and events. Count each numerator only within the same denominator cohort.

| Metric | Numerator | Denominator | Days | Anchor |
| --- | --- | --- | --- | --- |
| `application_rate` | submitted | unique_actionable_recommendations | 0 | none |
| `employer_response_rate` | employer_responses | submitted_mature_cohort | 30 | applied_at |
| `application_to_interview_rate` | interviews | submitted_mature_cohort | 30 | applied_at |
| `interview_to_offer_rate` | offers | interviews_mature_cohort | 30 | interview_at |
| `application_to_offer_rate` | offers | submitted_offer_mature_cohort | 60 | applied_at |

Submitted, interviews and offers use `applied_at`, `interview_at` and `offer_at` respectively; employer_responses uses `first_response_at`. Unique actionable recommendations include jobs that reached shortlist or later, including historical shortlist membership retained in events and explicitly submitted jobs. Screening eligibility alone is not a recommendation.

A mature cohort requires its anchor timestamp and either the configured elapsed days as of the reporting instant or a terminal outcome. Terminal outcomes `rejected`, `withdrawn` and `accepted` are mature without waiting for the age window; they do not create a missing anchor or numerator timestamp. Application rate has no maturity delay. Response and both interview-related rates default to 30 days; application-to-offer defaults to 60 days.

Candidate overrides may change maturity windows but must not redefine denominator populations or cohort anchors. The interview window applies to both interview-related rates. Record the selected windows and reporting instant; application rate remains at 0 days.

## Metric output

| Field | Meaning |
| --- | --- |
| `numerator` | Count with the required outcome evidence within the cohort. |
| `denominator` | Count of unique jobs in the specified cohort. |
| `maturity_window` | Object containing `days` and `terminal_outcome_is_mature: true`. |
| `rate` | `numerator / denominator`; if denominator is zero, rate is `null`. |
| `data_status` | `insufficient_data` for denominator below 10; otherwise `sufficient_data`. |

For each segment sample below 10, report `insufficient_data` and draw no strong conclusions. Show numerator, denominator and window alongside the rate; even sufficient sample size does not establish causation or interview probability.

The personal analytics builder reads the two tracker files and produces `reports/analytics-summary.json` and `reports/analytics-summary.md`. Preserve source traceability and `as_of`; do not manufacture outcomes to complete a funnel.

## Dashboard presentation

The Dashboard at `dashboard/dashboard.xlsx` is disposable, read-only presentation of local evidence. There is no workbook writeback to tracker or history; rebuild it after authorised source updates. Chat summaries can surface current actions without requiring the candidate to open the workbook.

The Dashboard reads exactly these three inputs:

| Input | Purpose |
| --- | --- |
| `tracker/applications.csv` | Current state, actions and weekly counts. |
| `tracker/events.jsonl` | Event links, reasons and document versions. |
| `reports/analytics-summary.json` | All conversion metrics, windows and data status. |

Use exactly these sheets in this order:

| Sheet | Purpose |
| --- | --- |
| Overview | Priorities, action counts and summary conversion metrics. |
| Applications | Current rows with document and event traceability. |
| Conversion | Summary metrics overall and by source, plus weekly tracker counts. |
| Follow-ups | Outstanding actions, due dates and blockers. |

If the analytics summary is missing or invalid, fail closed: no fallback metrics or recomputation from the workbook. Preserve the prior output and report the input problem. Each metric or row must retain its source path or event links for traceability. Do not read candidate documents, snapshots, other workspaces or an existing workbook as additional Dashboard inputs.
