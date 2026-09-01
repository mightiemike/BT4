# Q2420: l2 sync trusting an unsigned block via `create_circuit_input` (prover.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that the batch prover must consume when building circuit input, controlling the L1 payload the prover must ingest, drive `create_circuit_input` in `crates/batch-prover/src/prover.rs` so that the L2 blocks the prover proves over and the blocks covered by a signed commitment stop being the same set, breaking the invariant that proved blocks are commitment-covered?

## Target
- File/function: `crates/batch-prover/src/prover.rs` -> `create_circuit_input`
- Entrypoint: unprivileged party inscribes L1 data that the batch prover must consume when building circuit input
- Attacker controls: the L1 payload the prover must ingest
- Exploit idea: l2 sync trusting an unsigned block - reach `create_circuit_input` from that entrypoint and force the divergence where the L2 blocks the prover proves over and the blocks covered by a signed commitment stop being the same set; the adjacent symbols in the same file that carry the value are `ProverRequest`, `Prover`, `CommitmentStateTransitionData`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: proved blocks are commitment-covered
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed an uncommitted block and assert refusal
