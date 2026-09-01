# Q2011: proof session reuse via `new_from_env` (parallel.rs)

## Question
Can an unprivileged attacker who submits load that forces concurrent proving sessions over overlapping ranges, controlling the overlap between requested ranges, drive `new_from_env` in `crates/prover-services/src/parallel.rs` so that the input a proving session was started for and the input its output is attributed to stop being the same, breaking the invariant that each proof output is bound to its input?

## Target
- File/function: `crates/prover-services/src/parallel.rs` -> `new_from_env`
- Entrypoint: unprivileged party submits load that forces concurrent proving sessions over overlapping ranges
- Attacker controls: the overlap between requested ranges
- Exploit idea: proof session reuse - reach `new_from_env` from that entrypoint and force the divergence where the input a proving session was started for and the input its output is attributed to stop being the same; the adjacent symbols in the same file that carry the value are `ParallelProverService`, `prove`, `start_proving`, `reserve_proof_slot`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each proof output is bound to its input
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: interleave sessions and assert attribution
