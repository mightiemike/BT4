# Q2296: Coordinator sign setup - session persistence queue starvation

## Question
If a user create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, can `createSignSetup` be pushed into a path where persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry causes it to starve later outbounds or permanently jam the signing queue with one attacker-controlled flow, so that session-time verification must reconstruct the same transaction semantics the coordinator selected earlier no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:createSignSetup
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry
- Exploit idea: starve later outbounds or permanently jam the signing queue with one attacker-controlled flow
- Invariant to test: session-time verification must reconstruct the same transaction semantics the coordinator selected earlier
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: crash after setup, after signature persistence, and after broadcast; on restart, verify the recovered row neither double-signs nor loses the original outbound
