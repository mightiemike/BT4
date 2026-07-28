# Q2957: Coordinator in-flight count - deadline/expiry verification split

## Question
If a user create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, can `getInFlightSignCountPerChain` be pushed into a path where signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast causes it to make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign, so that nonce, signature, and eventstore state always belong to exactly one outbound at a time no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:getInFlightSignCountPerChain
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast
- Exploit idea: make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign
- Invariant to test: nonce, signature, and eventstore state always belong to exactly one outbound at a time
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: shorten deadlines while slowing broadcast or resolution and see whether one crafted outbound can trap many others in nonterminal states
