# Q2371: l2 sync trusting an unsigned block via `try_proving` (prover.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that the batch prover must consume when building circuit input, controlling which L1 hash a short header proof is requested for, drive `try_proving` in `crates/batch-prover/src/prover.rs` so that the L2 blocks the prover proves over and the blocks covered by a signed commitment stop being the same set, breaking the invariant that proved blocks are commitment-covered?

## Target
- File/function: `crates/batch-prover/src/prover.rs` -> `try_proving`
- Entrypoint: unprivileged party inscribes L1 data that the batch prover must consume when building circuit input
- Attacker controls: which L1 hash a short header proof is requested for
- Exploit idea: l2 sync trusting an unsigned block - reach `try_proving` from that entrypoint and force the divergence where the L2 blocks the prover proves over and the blocks covered by a signed commitment stop being the same set; the adjacent symbols in the same file that carry the value are `ProverRequest`, `Prover`, `CommitmentStateTransitionData`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: proved blocks are commitment-covered
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed an uncommitted block and assert refusal
