# Q1666: Cross-capability request reuse in ExecuteCapability

## Question
Can an unprivileged attacker reuse repeated or cross-capability execution requests with shared bytes at `POST /v2/execute_capability` so `ExecuteCapability` authorizes one capability name but replays bytes that execute under another handler, causing retrieve sensitive data from a capability that should be unreachable and violating unauthorized callers must never reach executable capability backends?

## Target
- File/function: core/web/capability_controller.go::ExecuteCapability
- Entrypoint: POST /v2/execute_capability
- Attacker controls: repeated or cross-capability execution requests with shared bytes
- Exploit idea: Replay serialized capability bytes across names and malformed payload variants to prove whether execution authority can widen beyond the named capability.
- Invariant to test: unauthorized callers must never reach executable capability backends
- Expected Immunefi impact: retrieve sensitive data from a capability that should be unreachable
- Fast validation: Send valid, partially valid, and cross-capability payloads; assert the named executable and actual executed backend always match.
