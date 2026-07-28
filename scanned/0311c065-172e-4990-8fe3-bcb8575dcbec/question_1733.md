# Q1733: Coordinator tx build - nonce assignment recovered double-sign

## Question
When an unprivileged actor create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, does `buildSignTransaction` remain safe if they control chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing, or can that make it recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states, violate the rule that session-time verification must reconstruct the same transaction semantics the coordinator selected earlier, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:buildSignTransaction
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing
- Exploit idea: recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states
- Invariant to test: session-time verification must reconstruct the same transaction semantics the coordinator selected earlier
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
