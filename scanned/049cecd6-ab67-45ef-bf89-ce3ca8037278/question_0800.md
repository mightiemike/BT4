# Q0800: Eventstore confirmed query - session persistence queue starvation

## Question
If a user submit many public Push-chain actions that create concurrent outbounds to the same destination chain, can `GetNonExpiredConfirmedEvents` be pushed into a path where persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry causes it to starve later outbounds or permanently jam the signing queue with one attacker-controlled flow, so that restart recovery never changes the signed meaning or multiplicity of an outbound already in flight no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/eventstore/store.go:GetNonExpiredConfirmedEvents
- Entrypoint: submit many public Push-chain actions that create concurrent outbounds to the same destination chain
- Attacker controls: persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry
- Exploit idea: starve later outbounds or permanently jam the signing queue with one attacker-controlled flow
- Invariant to test: restart recovery never changes the signed meaning or multiplicity of an outbound already in flight
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: shorten deadlines while slowing broadcast or resolution and see whether one crafted outbound can trap many others in nonterminal states
