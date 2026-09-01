# Q0855: commitment index gap resolution via `lib` (lib.rs)

## Question
Can an unprivileged attacker who inscribes commitments and proofs that induce a gap in the verified chain, controlling the initial and final roots the offered data claims, drive `lib` in `crates/light-client-prover/src/lib.rs` so that the index the circuit advances to and the highest index with a continuous verified chain stop being equal, breaking the invariant that advancement requires an unbroken verified chain?

## Target
- File/function: `crates/light-client-prover/src/lib.rs` -> `lib`
- Entrypoint: unprivileged party inscribes commitments and proofs that induce a gap in the verified chain
- Attacker controls: the initial and final roots the offered data claims
- Exploit idea: commitment index gap resolution - reach `lib` from that entrypoint and force the divergence where the index the circuit advances to and the highest index with a continuous verified chain stop being equal; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: advancement requires an unbroken verified chain
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: supply 3-4-5 and 7-8 and assert the advance stops at 5
