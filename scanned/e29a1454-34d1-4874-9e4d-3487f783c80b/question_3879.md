# Q3879: L1 reorg during commitment via `get_pending_mempool_commitments` (service.rs)

## Question
Can an unprivileged attacker who sends transactions sized to sit exactly at the commitment blob size threshold, controlling the L2 height at which its transactions land, drive `get_pending_mempool_commitments` in `crates/sequencer/src/commitment/service.rs` so that the L1 block a commitment was anchored to and the L1 block that ends up canonical stop being the same, breaking the invariant that commitments survive or are cleanly re-anchored across reorgs?

## Target
- File/function: `crates/sequencer/src/commitment/service.rs` -> `get_pending_mempool_commitments`
- Entrypoint: unprivileged party sends transactions sized to sit exactly at the commitment blob size threshold
- Attacker controls: the L2 height at which its transactions land
- Exploit idea: L1 reorg during commitment - reach `get_pending_mempool_commitments` from that entrypoint and force the divergence where the L1 block a commitment was anchored to and the L1 block that ends up canonical stop being the same; the adjacent symbols in the same file that carry the value are `CommitmentService`, `run`, `commit`, `store_commitments_from_da`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitments survive or are cleanly re-anchored across reorgs
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: reorg beneath a published commitment and assert recovery
