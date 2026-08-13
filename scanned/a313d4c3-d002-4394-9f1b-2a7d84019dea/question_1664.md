# Q1664: Boundary preservation edge case in ExecuteCapability #3

## Question
Can an unprivileged attacker use repeated or cross-capability execution requests with shared bytes at `POST /v2/execute_capability` so `ExecuteCapability` reaches a concrete path to execute arbitrary system commands through unsafe capability execution by breaking the invariant that capability name, request bytes, and executable target must stay bound to the same authorization decision, rather than merely causing an out-of-scope theoretical issue?

## Target
- File/function: core/web/capability_controller.go::ExecuteCapability
- Entrypoint: POST /v2/execute_capability
- Attacker controls: repeated or cross-capability execution requests with shared bytes
- Exploit idea: Replay serialized capability bytes across names and malformed payload variants to prove whether execution authority can widen beyond the named capability.
- Invariant to test: capability name, request bytes, and executable target must stay bound to the same authorization decision
- Expected Immunefi impact: execute arbitrary system commands through unsafe capability execution
- Fast validation: Send valid, partially valid, and cross-capability payloads; assert the named executable and actual executed backend always match.
