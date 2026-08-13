# Q1654: Boundary preservation edge case in Show #1

## Question
Can an unprivileged attacker use runID/taskID path params plus resume-result JSON payloads at `GET /v2/jobs/:ID/runs/:runID` so `Show` reaches a concrete path to execute arbitrary system commands through an unauthorized run path by breaking the invariant that resume/result binding must remain attached to the correct run and task IDs, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/pipeline_runs_controller.go::Show
- Entrypoint: GET /v2/jobs/:ID/runs/:runID
- Attacker controls: runID/taskID path params plus resume-result JSON payloads
- Exploit idea: Use real run IDs and resume payloads to prove whether unauthenticated or low-privilege callers can attach results or side effects to runs they do not own.
- Invariant to test: resume/result binding must remain attached to the correct run and task IDs
- Expected Immunefi impact: execute arbitrary system commands through an unauthorized run path
- Fast validation: Use real run IDs and repeated resume/create calls; assert only the owner/authorized caller can produce one side effect for one run.
