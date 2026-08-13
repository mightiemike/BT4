# Q1665: Capability request deserialization edge case in ExecuteCapability

## Question
Can an unprivileged attacker exploit malformed protobuf payloads that still partially deserialize at `POST /v2/execute_capability` so `ExecuteCapability` partially accepts malformed capability bytes and reaches privileged execution with different semantics than validation assumed, causing authentication bypass into privileged node actions and breaking deserialization must not broaden capability authority beyond what the request explicitly names?

## Target
- File/function: core/web/capability_controller.go::ExecuteCapability
- Entrypoint: POST /v2/execute_capability
- Attacker controls: malformed protobuf payloads that still partially deserialize
- Exploit idea: Replay serialized capability bytes across names and malformed payload variants to prove whether execution authority can widen beyond the named capability.
- Invariant to test: deserialization must not broaden capability authority beyond what the request explicitly names
- Expected Immunefi impact: authentication bypass into privileged node actions
- Fast validation: Send valid, partially valid, and cross-capability payloads; assert the named executable and actual executed backend always match.
