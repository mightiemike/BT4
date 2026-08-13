# Q1643: Repeat-execution race in Index

## Question
Can an unprivileged attacker abuse repeated resume or run-creation requests for the same object at `GET /v2/pipeline/runs or GET /v2/jobs/:ID/runs` so `Index` performs duplicate privileged side effects for one logical run, leading to theft of protocol revenue through repeated or confused execution and violating repeated run operations must not multiply privileged side effects?

## Target
- File/function: core/web/pipeline_runs_controller.go::Index
- Entrypoint: GET /v2/pipeline/runs or GET /v2/jobs/:ID/runs
- Attacker controls: repeated resume or run-creation requests for the same object
- Exploit idea: Use real run IDs and resume payloads to prove whether unauthenticated or low-privilege callers can attach results or side effects to runs they do not own.
- Invariant to test: repeated run operations must not multiply privileged side effects
- Expected Immunefi impact: theft of protocol revenue through repeated or confused execution
- Fast validation: Use real run IDs and repeated resume/create calls; assert only the owner/authorized caller can produce one side effect for one run.
