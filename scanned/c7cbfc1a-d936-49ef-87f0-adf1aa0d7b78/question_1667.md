# Q1667: Executable capability confusion in ExecuteCapability

## Question
Can an unprivileged attacker send capabilityName and serialized capabilityRequest bytes at `POST /v2/execute_capability` so `ExecuteCapability` resolves, deserializes, or executes a more dangerous capability path than intended, leading to execute arbitrary system commands through unsafe capability execution and violating capability name, request bytes, and executable target must stay bound to the same authorization decision?

## Target
- File/function: core/web/capability_controller.go::ExecuteCapability
- Entrypoint: POST /v2/execute_capability
- Attacker controls: capabilityName and serialized capabilityRequest bytes
- Exploit idea: Replay serialized capability bytes across names and malformed payload variants to prove whether execution authority can widen beyond the named capability.
- Invariant to test: capability name, request bytes, and executable target must stay bound to the same authorization decision
- Expected Immunefi impact: execute arbitrary system commands through unsafe capability execution
- Fast validation: Send valid, partially valid, and cross-capability payloads; assert the named executable and actual executed backend always match.
