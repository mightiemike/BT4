# Q0261: proof for a non-canonical L1 view via `try_proving` (prover.rs)

## Question
Can an unprivileged attacker who sends L2 transactions that force a specific proved range, controlling which L1 hash a short header proof is requested for, drive `try_proving` in `crates/batch-prover/src/prover.rs` so that the L1 chain the prover proved over and the chain the light client accepted stop being the same chain, breaking the invariant that proofs are anchored to the accepted L1 chain?

## Target
- File/function: `crates/batch-prover/src/prover.rs` -> `try_proving`
- Entrypoint: unprivileged party sends L2 transactions that force a specific proved range
- Attacker controls: which L1 hash a short header proof is requested for
- Exploit idea: proof for a non-canonical L1 view - reach `try_proving` from that entrypoint and force the divergence where the L1 chain the prover proved over and the chain the light client accepted stop being the same chain; the adjacent symbols in the same file that carry the value are `ProverRequest`, `Prover`, `CommitmentStateTransitionData`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: proofs are anchored to the accepted L1 chain
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: reorg the prover's view and assert the proof is refused
