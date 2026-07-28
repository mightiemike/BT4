# Q1827: Coordinator tx build - nonce assignment verification split

## Question
When an unprivileged actor create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, does `buildSignTransaction` remain safe if they control chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing, or can that make it make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign, violate the rule that one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:buildSignTransaction
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: chain-local nonce assignment, in-flight event counts, and the order in which confirmed outbounds are selected for signing
- Exploit idea: make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign
- Invariant to test: one attacker-controlled outbound cannot block unrelated user outbounds from reaching a terminal state
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: compare coordinator-built signing requests with sessionmanager verification output for the same outbound under edge-case fields
