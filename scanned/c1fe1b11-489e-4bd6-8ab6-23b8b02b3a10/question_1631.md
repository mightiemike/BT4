# Q1631: Boundary preservation edge case in Create #2

## Question
Can an unprivileged attacker use user-vs-token auth context on run creation at `POST /v2/jobs/:ID/runs` so `Create` reaches a concrete path to theft of protocol revenue through repeated or confused execution by breaking the invariant that repeated run operations must not multiply privileged side effects, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/pipeline_runs_controller.go::Create
- Entrypoint: POST /v2/jobs/:ID/runs
- Attacker controls: user-vs-token auth context on run creation
- Exploit idea: Use real run IDs and resume payloads to prove whether unauthenticated or low-privilege callers can attach results or side effects to runs they do not own.
- Invariant to test: repeated run operations must not multiply privileged side effects
- Expected Immunefi impact: theft of protocol revenue through repeated or confused execution
- Fast validation: Use real run IDs and repeated resume/create calls; assert only the owner/authorized caller can produce one side effect for one run.
