# Q2678: Session signing complete - deadline/expiry queue starvation

## Question
When an unprivileged actor create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, does `handleSigningComplete` remain safe if they control signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast, or can that make it starve later outbounds or permanently jam the signing queue with one attacker-controlled flow, violate the rule that restart recovery never changes the signed meaning or multiplicity of an outbound already in flight, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/sessionmanager/sessionmanager.go:handleSigningComplete
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast
- Exploit idea: starve later outbounds or permanently jam the signing queue with one attacker-controlled flow
- Invariant to test: restart recovery never changes the signed meaning or multiplicity of an outbound already in flight
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: enqueue several outbounds with controlled deadlines and payload sizes, then inspect nonce assignment, eventstore rows, and signing order under load
