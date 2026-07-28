# Q2768: Coordinator nonce assign - deadline/expiry cross-event nonce reuse

## Question
Can an unprivileged attacker create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls and use control over signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast so that `assignSignNonce` cause one outbound to reuse or consume signing state that should belong to a different outbound, breaking the invariant that session-time verification must reconstruct the same transaction semantics the coordinator selected earlier and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:assignSignNonce
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast
- Exploit idea: cause one outbound to reuse or consume signing state that should belong to a different outbound
- Invariant to test: session-time verification must reconstruct the same transaction semantics the coordinator selected earlier
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
