# Q1586: Command-capable spec expansion in Delete

## Question
Can an unprivileged attacker use numeric IDs, UUID-like values, and body identifiers bound to one job at `DELETE /v2/jobs/:ID` so `Delete` accepts spec content that reaches command execution, secret access, or privileged fetches beyond what authorization intended, causing authentication bypass into privileged node actions and violating job identifiers must remain bound to one real job throughout mutation and execution?

## Target
- File/function: core/web/jobs_controller.go::Delete
- Entrypoint: DELETE /v2/jobs/:ID
- Attacker controls: numeric IDs, UUID-like values, and body identifiers bound to one job
- Exploit idea: Feed attacker-controlled TOML/workflow data into the exact create/update path and confirm whether validated meaning matches executed behavior without silent privilege expansion.
- Invariant to test: job identifiers must remain bound to one real job throughout mutation and execution
- Expected Immunefi impact: authentication bypass into privileged node actions
- Fast validation: Submit the smallest adversarial spec/workflow through create/update and assert parsed meaning, spawned services, and side effects exactly match the authorized object.
