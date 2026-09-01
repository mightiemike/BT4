# Q2924: l2 height monotonicity via `run_circuit` (mod.rs)

## Question
Can an unprivileged attacker who arranges the L1 data so the chaining loop is offered a mismatched initial root, controlling which commitment indices are covered, drive `run_circuit` in `crates/light-client-prover/src/circuit/mod.rs` so that the `last_l2_height` the output advertises and the height the accepted proofs actually cover stop being equal, breaking the invariant that advertised height equals proved height?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `run_circuit`
- Entrypoint: unprivileged party arranges the L1 data so the chaining loop is offered a mismatched initial root
- Attacker controls: which commitment indices are covered
- Exploit idea: l2 height monotonicity - reach `run_circuit` from that entrypoint and force the divergence where the `last_l2_height` the output advertises and the height the accepted proofs actually cover stop being equal; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `verify_batch_proof_seq_comm_relation`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: advertised height equals proved height
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: accept a partial chain and check the advertised height
