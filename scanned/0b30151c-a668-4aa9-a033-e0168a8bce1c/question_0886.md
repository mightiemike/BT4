# Q0886: Coordinator sign setup - session persistence cross-event nonce reuse

## Question
If a user submit many public Push-chain actions that create concurrent outbounds to the same destination chain, can `createSignSetup` be pushed into a path where persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry causes it to cause one outbound to reuse or consume signing state that should belong to a different outbound, so that session-time verification must reconstruct the same transaction semantics the coordinator selected earlier no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:createSignSetup
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry
- Exploit idea: cause one outbound to reuse or consume signing state that should belong to a different outbound
- Invariant to test: session-time verification must reconstruct the same transaction semantics the coordinator selected earlier
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: enqueue several outbounds with controlled deadlines and payload sizes, then inspect nonce assignment, eventstore rows, and signing order under load
