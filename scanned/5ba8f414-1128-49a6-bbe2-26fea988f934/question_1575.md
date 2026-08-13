# Q1575: Boundary preservation edge case in Create #2

## Question
Can an unprivileged attacker use bridge names, external adapter URLs, and job-owned request metadata at `POST /v2/jobs` so `Create` reaches a concrete path to authentication bypass into privileged node actions by breaking the invariant that job identifiers must remain bound to one real job throughout mutation and execution, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/jobs_controller.go::Create
- Entrypoint: POST /v2/jobs
- Attacker controls: bridge names, external adapter URLs, and job-owned request metadata
- Exploit idea: Feed attacker-controlled TOML/workflow data into the exact create/update path and confirm whether validated meaning matches executed behavior without silent privilege expansion.
- Invariant to test: job identifiers must remain bound to one real job throughout mutation and execution
- Expected Immunefi impact: authentication bypass into privileged node actions
- Fast validation: Submit the smallest adversarial spec/workflow through create/update and assert parsed meaning, spawned services, and side effects exactly match the authorized object.
