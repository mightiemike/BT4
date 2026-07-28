# Q2586: Eventstore confirmed query - session persistence verification split

## Question
If a user create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, can `GetNonExpiredConfirmedEvents` be pushed into a path where persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry causes it to make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign, so that restart recovery never changes the signed meaning or multiplicity of an outbound already in flight no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/tss/eventstore/store.go:GetNonExpiredConfirmedEvents
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry
- Exploit idea: make the verifier accept a signing request whose semantics differ from what the coordinator originally intended to sign
- Invariant to test: restart recovery never changes the signed meaning or multiplicity of an outbound already in flight
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: enqueue several outbounds with controlled deadlines and payload sizes, then inspect nonce assignment, eventstore rows, and signing order under load
