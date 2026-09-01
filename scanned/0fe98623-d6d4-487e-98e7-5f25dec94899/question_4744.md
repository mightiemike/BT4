# Q4744: L1 reorg during commitment via `da_block_monitor` (da.rs)

## Question
Can an unprivileged attacker who broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to, controlling the L2 height at which its transactions land, drive `da_block_monitor` in `crates/sequencer/src/da.rs` so that the L1 block a commitment was anchored to and the L1 block that ends up canonical stop being the same, breaking the invariant that commitments survive or are cleanly re-anchored across reorgs?

## Target
- File/function: `crates/sequencer/src/da.rs` -> `da_block_monitor`
- Entrypoint: unprivileged party broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to
- Attacker controls: the L2 height at which its transactions land
- Exploit idea: L1 reorg during commitment - reach `da_block_monitor` from that entrypoint and force the divergence where the L1 block a commitment was anchored to and the L1 block that ends up canonical stop being the same; the adjacent symbols in the same file that carry the value are `fee_rate_monitor`, `get_finalized_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitments survive or are cleanly re-anchored across reorgs
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: reorg beneath a published commitment and assert recovery
