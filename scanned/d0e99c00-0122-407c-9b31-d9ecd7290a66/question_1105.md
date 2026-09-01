# Q1105: prior-output carry-over via `run_circuit` (mod.rs)

## Question
Can an unprivileged attacker who inscribes commitments and proofs that induce a gap in the verified chain, controlling which commitment indices are covered, drive `run_circuit` in `crates/light-client-prover/src/circuit/mod.rs` so that the previous output the circuit assumes and the previous output actually produced stop being the same journal, breaking the invariant that each proof chains to its true predecessor?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `run_circuit`
- Entrypoint: unprivileged party inscribes commitments and proofs that induce a gap in the verified chain
- Attacker controls: which commitment indices are covered
- Exploit idea: prior-output carry-over - reach `run_circuit` from that entrypoint and force the divergence where the previous output the circuit assumes and the previous output actually produced stop being the same journal; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `verify_batch_proof_seq_comm_relation`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each proof chains to its true predecessor
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed a mismatched previous output and assert rejection
