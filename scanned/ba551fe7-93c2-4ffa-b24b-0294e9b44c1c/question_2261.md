# Q2261: guest method id selection via `extract_output` (mod.rs)

## Question
Can an unprivileged attacker who makes the proved range span a fork or method-id activation boundary, controlling the overlap between requested ranges, drive `extract_output` in `crates/risc0/src/host/mod.rs` so that the guest method id used to prove and the id the light client will verify against stop being the same, breaking the invariant that provers and verifiers agree on the circuit?

## Target
- File/function: `crates/risc0/src/host/mod.rs` -> `extract_output`
- Entrypoint: unprivileged party makes the proved range span a fork or method-id activation boundary
- Attacker controls: the overlap between requested ranges
- Exploit idea: guest method id selection - reach `extract_output` from that entrypoint and force the divergence where the guest method id used to prove and the id the light client will verify against stop being the same; the adjacent symbols in the same file that carry the value are `Risc0Host`, `Prover`, `add_hint`, `simulate_with_hints`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: provers and verifiers agree on the circuit
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: prove across an activation boundary and assert verifiability
