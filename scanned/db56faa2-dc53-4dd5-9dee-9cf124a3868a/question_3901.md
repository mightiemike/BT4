# Q3901: Eventstore restart recover - session persistence cross-event nonce reuse

## Question
Can an unprivileged attacker start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED` and use control over persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry so that `RecoverInProgressEvents` cause one outbound to reuse or consume signing state that should belong to a different outbound, breaking the invariant that nonce, signature, and eventstore state always belong to exactly one outbound at a time and leading to widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/eventstore/store.go:RecoverInProgressEvents
- Entrypoint: start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`
- Attacker controls: persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry
- Exploit idea: cause one outbound to reuse or consume signing state that should belong to a different outbound
- Invariant to test: nonce, signature, and eventstore state always belong to exactly one outbound at a time
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: compare coordinator-built signing requests with sessionmanager verification output for the same outbound under edge-case fields
