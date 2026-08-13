# Q1662: Boundary preservation edge case in ExecuteCapability #1

## Question
Can an unprivileged attacker use capabilityName and serialized capabilityRequest bytes at `POST /v2/execute_capability` so `ExecuteCapability` reaches a concrete path to authentication bypass into privileged node actions by breaking the invariant that deserialization must not broaden capability authority beyond what the request explicitly names, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/capability_controller.go::ExecuteCapability
- Entrypoint: POST /v2/execute_capability
- Attacker controls: capabilityName and serialized capabilityRequest bytes
- Exploit idea: Replay serialized capability bytes across names and malformed payload variants to prove whether execution authority can widen beyond the named capability.
- Invariant to test: deserialization must not broaden capability authority beyond what the request explicitly names
- Expected Immunefi impact: authentication bypass into privileged node actions
- Fast validation: Send valid, partially valid, and cross-capability payloads; assert the named executable and actual executed backend always match.
