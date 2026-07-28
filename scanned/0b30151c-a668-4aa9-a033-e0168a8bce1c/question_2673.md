# Q2673: Coordinator tx build - deadline/expiry queue starvation

## Question
If a user create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls, can `buildSignTransaction` be pushed into a path where signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast causes it to starve later outbounds or permanently jam the signing queue with one attacker-controlled flow, so that restart recovery never changes the signed meaning or multiplicity of an outbound already in flight no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/coordinator/coordinator.go:buildSignTransaction
- Entrypoint: create one public outbound with edge-case IDs, payload size, gas values, or deadlines that still fits normal user controls
- Attacker controls: signing deadline, block height, and expiry timing as they interact with session cleanup and rebroadcast
- Exploit idea: starve later outbounds or permanently jam the signing queue with one attacker-controlled flow
- Invariant to test: restart recovery never changes the signed meaning or multiplicity of an outbound already in flight
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: enqueue several outbounds with controlled deadlines and payload sizes, then inspect nonce assignment, eventstore rows, and signing order under load
