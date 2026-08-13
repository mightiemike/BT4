# Q1632: Boundary preservation edge case in Create #3

## Question
Can an unprivileged attacker use repeated resume or run-creation requests for the same object at `POST /v2/jobs/:ID/runs` so `Create` reaches a concrete path to unauthorized pipeline execution or resume with privileged side effects by breaking the invariant that only authorized callers may create, resume, or observe a run with side effects, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/pipeline_runs_controller.go::Create
- Entrypoint: POST /v2/jobs/:ID/runs
- Attacker controls: repeated resume or run-creation requests for the same object
- Exploit idea: Use real run IDs and resume payloads to prove whether unauthenticated or low-privilege callers can attach results or side effects to runs they do not own.
- Invariant to test: only authorized callers may create, resume, or observe a run with side effects
- Expected Immunefi impact: unauthorized pipeline execution or resume with privileged side effects
- Fast validation: Use real run IDs and repeated resume/create calls; assert only the owner/authorized caller can produce one side effect for one run.
