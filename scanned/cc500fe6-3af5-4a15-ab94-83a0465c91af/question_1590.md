# Q1590: Boundary preservation edge case in Index #1

## Question
Can an unprivileged attacker use job TOML, relay config, pipeline task params, and workflow/wasm references at `GET /v2/jobs` so `Index` reaches a concrete path to retrieve sensitive data/files from a running server such as database passwords and blockchain keys by breaking the invariant that untrusted spec content must not silently expand into command execution, privileged fetches, or secret access, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/jobs_controller.go::Index
- Entrypoint: GET /v2/jobs
- Attacker controls: job TOML, relay config, pipeline task params, and workflow/wasm references
- Exploit idea: Feed attacker-controlled TOML/workflow data into the exact create/update path and confirm whether validated meaning matches executed behavior without silent privilege expansion.
- Invariant to test: untrusted spec content must not silently expand into command execution, privileged fetches, or secret access
- Expected Immunefi impact: retrieve sensitive data/files from a running server such as database passwords and blockchain keys
- Fast validation: Submit the smallest adversarial spec/workflow through create/update and assert parsed meaning, spawned services, and side effects exactly match the authorized object.
