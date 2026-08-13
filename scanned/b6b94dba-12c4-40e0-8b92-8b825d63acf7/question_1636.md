# Q1636: Run/result binding bug in Create

## Question
Can an unprivileged attacker exploit user-vs-token auth context on run creation at `POST /v2/jobs/:ID/runs` so `Create` attaches a resume result or side effect to the wrong run/task, causing execute arbitrary system commands through an unauthorized run path and breaking resume/result binding must remain attached to the correct run and task IDs?

## Target
- File/function: core/web/pipeline_runs_controller.go::Create
- Entrypoint: POST /v2/jobs/:ID/runs
- Attacker controls: user-vs-token auth context on run creation
- Exploit idea: Use real run IDs and resume payloads to prove whether unauthenticated or low-privilege callers can attach results or side effects to runs they do not own.
- Invariant to test: resume/result binding must remain attached to the correct run and task IDs
- Expected Immunefi impact: execute arbitrary system commands through an unauthorized run path
- Fast validation: Use real run IDs and repeated resume/create calls; assert only the owner/authorized caller can produce one side effect for one run.
