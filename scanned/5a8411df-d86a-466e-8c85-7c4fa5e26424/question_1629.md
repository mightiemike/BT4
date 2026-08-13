# Q1629: Unauthorized run creation or resume in Destroy

## Question
Can an unprivileged attacker use runID/taskID path params plus resume-result JSON payloads at `PATCH /v2/resume/:runID or POST /v2/jobs/:ID/runs` so `Destroy` creates or resumes a run they should not control, leading to unauthorized pipeline execution or resume with privileged side effects and violating only authorized callers may create, resume, or observe a run with side effects?

## Target
- File/function: core/web/pipeline_job_spec_errors_controller.go::Destroy
- Entrypoint: PATCH /v2/resume/:runID or POST /v2/jobs/:ID/runs
- Attacker controls: runID/taskID path params plus resume-result JSON payloads
- Exploit idea: Use real run IDs and resume payloads to prove whether unauthenticated or low-privilege callers can attach results or side effects to runs they do not own.
- Invariant to test: only authorized callers may create, resume, or observe a run with side effects
- Expected Immunefi impact: unauthorized pipeline execution or resume with privileged side effects
- Fast validation: Use real run IDs and repeated resume/create calls; assert only the owner/authorized caller can produce one side effect for one run.
