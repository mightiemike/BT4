# Q0051: guest method id selection via `register_ethereum` (eth.rs)

## Question
Can an unprivileged attacker who submits load that forces concurrent proving sessions over overlapping ranges, controlling the overlap between requested ranges, drive `register_ethereum` in `bin/citrea/src/eth.rs` so that the guest method id used to prove and the id the light client will verify against stop being the same, breaking the invariant that provers and verifiers agree on the circuit?

## Target
- File/function: `bin/citrea/src/eth.rs` -> `register_ethereum`
- Entrypoint: unprivileged party submits load that forces concurrent proving sessions over overlapping ranges
- Attacker controls: the overlap between requested ranges
- Exploit idea: guest method id selection - reach `register_ethereum` from that entrypoint and force the divergence where the guest method id used to prove and the id the light client will verify against stop being the same; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: provers and verifiers agree on the circuit
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: prove across an activation boundary and assert verifiability
