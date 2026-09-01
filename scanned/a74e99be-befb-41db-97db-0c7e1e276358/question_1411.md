# Q1411: proof session reuse via `run` (mod.rs)

## Question
Can an unprivileged attacker who submits load that forces concurrent proving sessions over overlapping ranges, controlling the activation boundary the range spans, drive `run` in `crates/risc0/src/host/mod.rs` so that the input a proving session was started for and the input its output is attributed to stop being the same, breaking the invariant that each proof output is bound to its input?

## Target
- File/function: `crates/risc0/src/host/mod.rs` -> `run`
- Entrypoint: unprivileged party submits load that forces concurrent proving sessions over overlapping ranges
- Attacker controls: the activation boundary the range spans
- Exploit idea: proof session reuse - reach `run` from that entrypoint and force the divergence where the input a proving session was started for and the input its output is attributed to stop being the same; the adjacent symbols in the same file that carry the value are `Risc0Host`, `Prover`, `add_hint`, `simulate_with_hints`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each proof output is bound to its input
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: interleave sessions and assert attribution
