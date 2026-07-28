# Q0235: Eventstore restart recover - nonce assignment recovered double-sign

## Question
If a user submit many public Push-chain actions that create concurrent outbounds to the same destination chain, can `RecoverInProgressEvents` be pushed into a path where chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing causes it to recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states, so that restart recovery never changes the signed meaning or multiplicity of an outbound already in flight no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/eventstore/store.go:RecoverInProgressEvents
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing
- Exploit idea: recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states
- Invariant to test: restart recovery never changes the signed meaning or multiplicity of an outbound already in flight
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: shorten deadlines while slowing broadcast or resolution and see whether one crafted outbound can trap many others in nonterminal states
