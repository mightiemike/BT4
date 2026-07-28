# Q3339: Eventstore stale unsigned cleanup - nonce assignment verification split

## Question
If a user start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`, can `DeleteOldUnsignedEvents` be pushed into a path where chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing causes it to make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign, so that nonce, signature, and eventstore state always belong to exactly one outbound at a time no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/eventstore/store.go:DeleteOldUnsignedEvents
- Entrypoint: start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`
- Attacker controls: chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing
- Exploit idea: make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign
- Invariant to test: nonce, signature, and eventstore state always belong to exactly one outbound at a time
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: compare coordinator-built signing requests with sessionmanager verification output for the same outbound under edge-case fields
