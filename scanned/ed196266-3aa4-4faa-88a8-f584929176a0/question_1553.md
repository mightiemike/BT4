# Q1553: Eventstore stale unsigned cleanup - nonce assignment queue starvation

## Question
If a user create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, can `DeleteOldUnsignedEvents` be pushed into a path where chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing causes it to starve later outbounds or permanently jam the signing queue with one attacker-controlled flow, so that nonce, signature, and eventstore state always belong to exactly one outbound at a time no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/eventstore/store.go:DeleteOldUnsignedEvents
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing
- Exploit idea: starve later outbounds or permanently jam the signing queue with one attacker-controlled flow
- Invariant to test: nonce, signature, and eventstore state always belong to exactly one outbound at a time
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: shorten deadlines while slowing broadcast or resolution and see whether one crafted outbound can trap many others in nonterminal states
