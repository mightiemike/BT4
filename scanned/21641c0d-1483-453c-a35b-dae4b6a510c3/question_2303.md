# Q2303: Eventstore restart recover - session persistence queue starvation

## Question
When an unprivileged actor create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, does `RecoverInProgressEvents` remain safe if they control persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry, or can that make it starve later outbounds or permanently jam the signing queue with one attacker-controlled flow, violate the rule that session-time verification must reconstruct the same transaction semantics the coordinator selected earlier, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/eventstore/store.go:RecoverInProgressEvents
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry
- Exploit idea: starve later outbounds or permanently jam the signing queue with one attacker-controlled flow
- Invariant to test: session-time verification must reconstruct the same transaction semantics the coordinator selected earlier
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
