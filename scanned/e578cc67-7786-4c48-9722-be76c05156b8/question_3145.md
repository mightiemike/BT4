# Q3145: Coordinator in-flight count - nonce assignment cross-event nonce reuse

## Question
Can an unprivileged attacker start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED` and use control over chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing so that `getInFlightSignCountPerChain` cause one outbound to reuse or consume signing state that should belong to a different outbound, breaking the invariant that session-time verification must reconstruct the same transaction semantics the coordinator selected earlier and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:getInFlightSignCountPerChain
- Entrypoint: start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`
- Attacker controls: chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing
- Exploit idea: cause one outbound to reuse or consume signing state that should belong to a different outbound
- Invariant to test: session-time verification must reconstruct the same transaction semantics the coordinator selected earlier
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: enqueue several outbounds with controlled deadlines and payload sizes, then inspect nonce assignment, eventstore rows, and signing order under load
