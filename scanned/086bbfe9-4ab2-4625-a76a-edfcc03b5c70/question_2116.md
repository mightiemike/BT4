# Q2116: Eventstore confirmed query - sign setup data recovered double-sign

## Question
If a user create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, can `GetNonExpiredConfirmedEvents` be pushed into a path where the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data causes it to recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states, so that restart recovery never changes the signed meaning or multiplicity of an outbound already in flight no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/eventstore/store.go:GetNonExpiredConfirmedEvents
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: the bytes carried into `createSignSetup`, `buildSignTransaction`, and session creation from attacker-controlled outbound data
- Exploit idea: recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states
- Invariant to test: restart recovery never changes the signed meaning or multiplicity of an outbound already in flight
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: enqueue several outbounds with controlled deadlines and payload sizes, then inspect nonce assignment, eventstore rows, and signing order under load
