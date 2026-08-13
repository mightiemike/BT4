# Q1663: Boundary preservation edge case in ExecuteCapability #2

## Question
Can an unprivileged attacker use malformed protobuf payloads that still partially deserialize at `POST /v2/execute_capability` so `ExecuteCapability` reaches a concrete path to retrieve sensitive data from a capability that should be unreachable by breaking the invariant that unauthorized callers must never reach executable capability backends, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/capability_controller.go::ExecuteCapability
- Entrypoint: POST /v2/execute_capability
- Attacker controls: malformed protobuf payloads that still partially deserialize
- Exploit idea: Replay serialized capability bytes across names and malformed payload variants to prove whether execution authority can widen beyond the named capability.
- Invariant to test: unauthorized callers must never reach executable capability backends
- Expected Immunefi impact: retrieve sensitive data from a capability that should be unreachable
- Fast validation: Send valid, partially valid, and cross-capability payloads; assert the named executable and actual executed backend always match.
