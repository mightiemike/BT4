# Q1589: Unauthorized job creation or mutation in Delete

## Question
Can an unprivileged attacker submit job TOML, relay config, pipeline task params, and workflow/wasm references at `DELETE /v2/jobs/:ID` so `Delete` creates, updates, or deletes a job/workflow without the intended privilege boundary, leading to execute arbitrary system commands through a newly reachable privileged job or workflow path and violating spec validation and downstream execution must agree on the exact workflow, pipeline, and adapter behavior being authorized?

## Target
- File/function: core/web/jobs_controller.go::Delete
- Entrypoint: DELETE /v2/jobs/:ID
- Attacker controls: job TOML, relay config, pipeline task params, and workflow/wasm references
- Exploit idea: Feed attacker-controlled TOML/workflow data into the exact create/update path and confirm whether validated meaning matches executed behavior without silent privilege expansion.
- Invariant to test: spec validation and downstream execution must agree on the exact workflow, pipeline, and adapter behavior being authorized
- Expected Immunefi impact: execute arbitrary system commands through a newly reachable privileged job or workflow path
- Fast validation: Submit the smallest adversarial spec/workflow through create/update and assert parsed meaning, spawned services, and side effects exactly match the authorized object.
