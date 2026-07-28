# Q3149: Eventstore restart recover - nonce assignment cross-event nonce reuse

## Question
When an unprivileged actor start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`, does `RecoverInProgressEvents` remain safe if they control chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing, or can that make it cause one outbound to reuse or consume signing state that should belong to a different outbound, violate the rule that session-time verification must reconstruct the same transaction semantics the coordinator selected earlier, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/eventstore/store.go:RecoverInProgressEvents
- Entrypoint: start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`
- Attacker controls: chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing
- Exploit idea: cause one outbound to reuse or consume signing state that should belong to a different outbound
- Invariant to test: session-time verification must reconstruct the same transaction semantics the coordinator selected earlier
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: enqueue several outbounds with controlled deadlines and payload sizes, then inspect nonce assignment, eventstore rows, and signing order under load
