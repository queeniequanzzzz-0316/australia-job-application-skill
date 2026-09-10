# Expected Invariants

These invariants apply to every evaluation case and use only public or synthetic fixtures.

- **INV-01 Australia-only:** Search results and applications must be for roles located in Australia.
- **INV-02 Onshore/offshore:** The profile must distinguish onshore from offshore candidates and must not infer location status.
- **INV-03 Professional/casual-part-time:** Onboarding and filtering must distinguish professional roles from casual or part-time roles.
- **INV-04 No fabrication:** Candidate claims, role facts, and application content must be supported by supplied or verified evidence; fabrication is prohibited.
- **INV-05 Evidence Audit:** Every material candidate claim and screening decision must identify its evidence or be marked unsupported or unknown.
- **INV-06 Confirmed candidate profile and confirmed search filters:** Both must be explicitly confirmed before Discovery begins.
- **INV-07 Discovery/Verification/Hard Gate/Top N:** Roles must pass each preceding stage before entering Top N; stages and rejection reasons must be recorded.
- **INV-08 HR + Hiring Manager perspectives:** Each shortlisted role must include separate HR and Hiring Manager assessments grounded in the same evidence.
- **INV-09 Assisted apply stops before Submit:** Assistance may prepare and populate an application, but the candidate must perform final Submit.
- **INV-10 Current-state tracker + append-only event log:** The workflow must maintain both, and history must not be overwritten.
- **INV-11 Separately authorized anonymous analytics:** Analytics may be shared only after separate, explicit authorization and must contain no identifiable candidate data.
- **INV-12 Explicitly authorized morning/evening automations:** Morning and evening automations may be created or enabled only after explicit authorization for the applicable schedule.
- **INV-13 JD snapshot traceability:** Every shortlisted role must link to a dated JD snapshot with its public source URL and retrieval time.
