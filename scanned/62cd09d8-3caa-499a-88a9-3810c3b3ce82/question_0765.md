# Q0765: l2 height monotonicity via `verify_batch_proof_seq_comm_relation` (mod.rs)

## Question
Can an unprivileged attacker who inscribes commitments and proofs that induce a gap in the verified chain, controlling which commitment indices are covered, drive `verify_batch_proof_seq_comm_relation` in `crates/light-client-prover/src/circuit/mod.rs` so that the `last_l2_height` the output advertises and the height the accepted proofs actually cover stop being equal, breaking the invariant that advertised height equals proved height?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `verify_batch_proof_seq_comm_relation`
- Entrypoint: unprivileged party inscribes commitments and proofs that induce a gap in the verified chain
- Attacker controls: which commitment indices are covered
- Exploit idea: l2 height monotonicity - reach `verify_batch_proof_seq_comm_relation` from that entrypoint and force the divergence where the `last_l2_height` the output advertises and the height the accepted proofs actually cover stop being equal; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `process_complete_proof`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: advertised height equals proved height
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: accept a partial chain and check the advertised height
