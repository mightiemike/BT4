# Q2735: l2 sync trusting an unsigned block via `watch_proving_jobs` (prover.rs)

## Question
Can an unprivileged attacker who forces the prover to request a short header proof for an L1 hash of its choosing, controlling which L1 hash a short header proof is requested for, drive `watch_proving_jobs` in `crates/batch-prover/src/prover.rs` so that the L2 blocks the prover proves over and the blocks covered by a signed commitment stop being the same set, breaking the invariant that proved blocks are commitment-covered?

## Target
- File/function: `crates/batch-prover/src/prover.rs` -> `watch_proving_jobs`
- Entrypoint: unprivileged party forces the prover to request a short header proof for an L1 hash of its choosing
- Attacker controls: which L1 hash a short header proof is requested for
- Exploit idea: l2 sync trusting an unsigned block - reach `watch_proving_jobs` from that entrypoint and force the divergence where the L2 blocks the prover proves over and the blocks covered by a signed commitment stop being the same set; the adjacent symbols in the same file that carry the value are `ProverRequest`, `Prover`, `CommitmentStateTransitionData`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: proved blocks are commitment-covered
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed an uncommitted block and assert refusal
