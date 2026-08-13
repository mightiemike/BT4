# Q1650: Identifier parsing confusion in Resume

## Question
Can an unprivileged attacker shape numeric-vs-UUID job/run identifier parsing at `PATCH /v2/resume/:runID` so `Resume` rejects or authorizes using one run/job identifier form but executes using another, causing unauthorized pipeline execution or resume with privileged side effects and violating only authorized callers may create, resume, or observe a run with side effects?

## Target
- File/function: core/web/pipeline_runs_controller.go::Resume
- Entrypoint: PATCH /v2/resume/:runID
- Attacker controls: numeric-vs-UUID job/run identifier parsing
- Exploit idea: Use real run IDs and resume payloads to prove whether unauthenticated or low-privilege callers can attach results or side effects to runs they do not own.
- Invariant to test: only authorized callers may create, resume, or observe a run with side effects
- Expected Immunefi impact: unauthorized pipeline execution or resume with privileged side effects
- Fast validation: Use real run IDs and repeated resume/create calls; assert only the owner/authorized caller can produce one side effect for one run.
