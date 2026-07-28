# Q3990: Coordinator nonce assign - session persistence recovered double-sign

## Question
When an unprivileged actor start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`, does `assignSignNonce` remain safe if they control persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry, or can that make it recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states, violate the rule that restart recovery never changes the signed meaning or multiplicity of an outbound already in flight, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:assignSignNonce
- Entrypoint: start a user-controlled outbound, then restart a validator while it is between `CONFIRMED`, `SIGNED`, and `BROADCASTED`
- Attacker controls: persisted eventstore rows, signed payloads, and recovery behavior after a crash or retry
- Exploit idea: recreate an in-flight event after restart so the same economic outbound is signed or broadcast twice under different local states
- Invariant to test: restart recovery never changes the signed meaning or multiplicity of an outbound already in flight
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: shorten deadlines while slowing broadcast or resolution and see whether one crafted outbound can trap many others in nonterminal states
