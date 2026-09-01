# Q2618: proof for a non-canonical L1 view via `run` (prover.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that the batch prover must consume when building circuit input, controlling which L1 hash a short header proof is requested for, drive `run` in `crates/batch-prover/src/prover.rs` so that the L1 chain the prover proved over and the chain the light client accepted stop being the same chain, breaking the invariant that proofs are anchored to the accepted L1 chain?

## Target
- File/function: `crates/batch-prover/src/prover.rs` -> `run`
- Entrypoint: unprivileged party inscribes L1 data that the batch prover must consume when building circuit input
- Attacker controls: which L1 hash a short header proof is requested for
- Exploit idea: proof for a non-canonical L1 view - reach `run` from that entrypoint and force the divergence where the L1 chain the prover proved over and the chain the light client accepted stop being the same chain; the adjacent symbols in the same file that carry the value are `ProverRequest`, `Prover`, `CommitmentStateTransitionData`, `try_proving`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: proofs are anchored to the accepted L1 chain
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: reorg the prover's view and assert the proof is refused
