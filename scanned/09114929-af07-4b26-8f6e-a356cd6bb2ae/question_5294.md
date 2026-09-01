# Q5294: L1 reorg during commitment via `store_commitments_from_da` (service.rs)

## Question
Can an unprivileged attacker who submits transactions that land in the last L2 block before a sequencer commitment is produced, controlling reorg depth achievable with valid Bitcoin transactions, drive `store_commitments_from_da` in `crates/sequencer/src/commitment/service.rs` so that the L1 block a commitment was anchored to and the L1 block that ends up canonical stop being the same, breaking the invariant that commitments survive or are cleanly re-anchored across reorgs?

## Target
- File/function: `crates/sequencer/src/commitment/service.rs` -> `store_commitments_from_da`
- Entrypoint: unprivileged party submits transactions that land in the last L2 block before a sequencer commitment is produced
- Attacker controls: reorg depth achievable with valid Bitcoin transactions
- Exploit idea: L1 reorg during commitment - reach `store_commitments_from_da` from that entrypoint and force the divergence where the L1 block a commitment was anchored to and the L1 block that ends up canonical stop being the same; the adjacent symbols in the same file that carry the value are `CommitmentService`, `run`, `commit`, `get_commitment`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitments survive or are cleanly re-anchored across reorgs
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: reorg beneath a published commitment and assert recovery
