# Q0521: l2 sync trusting an unsigned block via `resubmit_pending_l1_proofs` (prover.rs)

## Question
Can an unprivileged attacker who forces the prover to request a short header proof for an L1 hash of its choosing, controlling the commitment range boundaries, drive `resubmit_pending_l1_proofs` in `crates/batch-prover/src/prover.rs` so that the L2 blocks the prover proves over and the blocks covered by a signed commitment stop being the same set, breaking the invariant that proved blocks are commitment-covered?

## Target
- File/function: `crates/batch-prover/src/prover.rs` -> `resubmit_pending_l1_proofs`
- Entrypoint: unprivileged party forces the prover to request a short header proof for an L1 hash of its choosing
- Attacker controls: the commitment range boundaries
- Exploit idea: l2 sync trusting an unsigned block - reach `resubmit_pending_l1_proofs` from that entrypoint and force the divergence where the L2 blocks the prover proves over and the blocks covered by a signed commitment stop being the same set; the adjacent symbols in the same file that carry the value are `ProverRequest`, `Prover`, `CommitmentStateTransitionData`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: proved blocks are commitment-covered
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed an uncommitted block and assert refusal
