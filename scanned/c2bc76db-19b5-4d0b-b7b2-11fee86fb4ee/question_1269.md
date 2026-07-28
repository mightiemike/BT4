# Q1269: Eventstore restart recover - deadline/expiry cross-event nonce reuse

## Question
If a user submit many public Push-chain actions that create concurrent outbounds to the same destination chain, can `RecoverInProgressEvents` be pushed into a path where signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast causes it to cause one outbound to reuse or consume signing state that should belong to a different outbound, so that restart recovery never changes the signed meaning or multiplicity of an outbound already in flight no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/eventstore/store.go:RecoverInProgressEvents
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast
- Exploit idea: cause one outbound to reuse or consume signing state that should belong to a different outbound
- Invariant to test: restart recovery never changes the signed meaning or multiplicity of an outbound already in flight
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: shorten deadlines while slowing broadcast or resolution and see whether one crafted outbound can trap many others in nonterminal states
