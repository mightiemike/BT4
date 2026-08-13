# Q1612: Spec parsing differential in Update

## Question
Can an unprivileged attacker craft bridge names, external adapter URLs, and job-owned request metadata at `PUT /v2/jobs/:ID` so `Update` validates one job/workflow meaning while downstream execution interprets another, causing retrieve sensitive data/files from a running server such as database passwords and blockchain keys and breaking untrusted spec content must not silently expand into command execution, privileged fetches, or secret access?

## Target
- File/function: core/web/jobs_controller.go::Update
- Entrypoint: PUT /v2/jobs/:ID
- Attacker controls: bridge names, external adapter URLs, and job-owned request metadata
- Exploit idea: Feed attacker-controlled TOML/workflow data into the exact create/update path and confirm whether validated meaning matches executed behavior without silent privilege expansion.
- Invariant to test: untrusted spec content must not silently expand into command execution, privileged fetches, or secret access
- Expected Immunefi impact: retrieve sensitive data/files from a running server such as database passwords and blockchain keys
- Fast validation: Submit the smallest adversarial spec/workflow through create/update and assert parsed meaning, spawned services, and side effects exactly match the authorized object.
