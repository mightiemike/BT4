# Q0140: Session signing complete - nonce assignment cross-event nonce reuse

## Question
If a user submit many public Push-chain actions that create concurrent outbounds to the same destination chain, can `handleSigningComplete` be pushed into a path where chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing causes it to cause one outbound to reuse or consume signing state that should belong to a different outbound, so that nonce, signature, and eventstore state always belong to exactly one outbound at a time no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/sessionmanager/sessionmanager.go:handleSigningComplete
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing
- Exploit idea: cause one outbound to reuse or consume signing state that should belong to a different outbound
- Invariant to test: nonce, signature, and eventstore state always belong to exactly one outbound at a time
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: compare coordinator-built signing requests with sessionmanager verification output for the same outbound under edge-case fields
