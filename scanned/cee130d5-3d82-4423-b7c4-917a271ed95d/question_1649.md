# Q1649: Boundary preservation edge case in Resume #4

## Question
Can an unprivileged attacker use numeric-vs-UUID job/run identifier parsing at `PATCH /v2/resume/:runID` so `Resume` reaches a concrete path to execute arbitrary system commands through an unauthorized run path by breaking the invariant that resume/result binding must remain attached to the correct run and task IDs, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/pipeline_runs_controller.go::Resume
- Entrypoint: PATCH /v2/resume/:runID
- Attacker controls: numeric-vs-UUID job/run identifier parsing
- Exploit idea: Use real run IDs and resume payloads to prove whether unauthenticated or low-privilege callers can attach results or side effects to runs they do not own.
- Invariant to test: resume/result binding must remain attached to the correct run and task IDs
- Expected Immunefi impact: execute arbitrary system commands through an unauthorized run path
- Fast validation: Use real run IDs and repeated resume/create calls; assert only the owner/authorized caller can produce one side effect for one run.
