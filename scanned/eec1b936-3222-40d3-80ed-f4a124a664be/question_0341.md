# Q0341: proof for a non-canonical L1 view via `filter_commitments_with_index_gap` (prover.rs)

## Question
Can an unprivileged attacker who forces the prover to request a short header proof for an L1 hash of its choosing, controlling the commitment range boundaries, drive `filter_commitments_with_index_gap` in `crates/batch-prover/src/prover.rs` so that the L1 chain the prover proved over and the chain the light client accepted stop being the same chain, breaking the invariant that proofs are anchored to the accepted L1 chain?

## Target
- File/function: `crates/batch-prover/src/prover.rs` -> `filter_commitments_with_index_gap`
- Entrypoint: unprivileged party forces the prover to request a short header proof for an L1 hash of its choosing
- Attacker controls: the commitment range boundaries
- Exploit idea: proof for a non-canonical L1 view - reach `filter_commitments_with_index_gap` from that entrypoint and force the divergence where the L1 chain the prover proved over and the chain the light client accepted stop being the same chain; the adjacent symbols in the same file that carry the value are `ProverRequest`, `Prover`, `CommitmentStateTransitionData`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: proofs are anchored to the accepted L1 chain
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: reorg the prover's view and assert the proof is refused
