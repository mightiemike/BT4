# Q1655: l2 height monotonicity via `build_services` (services.rs)

## Question
Can an unprivileged attacker who inscribes commitments and proofs that induce a gap in the verified chain, controlling which commitment indices are covered, drive `build_services` in `crates/light-client-prover/src/services.rs` so that the `last_l2_height` the output advertises and the height the accepted proofs actually cover stop being equal, breaking the invariant that advertised height equals proved height?

## Target
- File/function: `crates/light-client-prover/src/services.rs` -> `build_services`
- Entrypoint: unprivileged party inscribes commitments and proofs that induce a gap in the verified chain
- Attacker controls: which commitment indices are covered
- Exploit idea: l2 height monotonicity - reach `build_services` from that entrypoint and force the divergence where the `last_l2_height` the output advertises and the height the accepted proofs actually cover stop being equal; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: advertised height equals proved height
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: accept a partial chain and check the advertised height
