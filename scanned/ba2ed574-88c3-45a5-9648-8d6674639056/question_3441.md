# Q3441: stale-node pruning versus proof availability via `get_sequencer_commitment_by_index` (rpc.rs)

## Question
Can an unprivileged attacker who inscribes data that lands in a persisted table keyed by an attacker-influenced index, controlling the index or key an attacker-supplied field derives, drive `get_sequencer_commitment_by_index` in `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` so that the nodes pruned and the nodes no proof will ever need stop being the same set, breaking the invariant that pruning never removes state a pending proof requires?

## Target
- File/function: `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` -> `get_sequencer_commitment_by_index`
- Entrypoint: unprivileged party inscribes data that lands in a persisted table keyed by an attacker-influenced index
- Attacker controls: the index or key an attacker-supplied field derives
- Exploit idea: stale-node pruning versus proof availability - reach `get_sequencer_commitment_by_index` from that entrypoint and force the divergence where the nodes pruned and the nodes no proof will ever need stop being the same set; the adjacent symbols in the same file that carry the value are `check_if_l2_block_pruned`, `get_l2_block`, `get_l2_block_by_hash`, `get_l2_block_by_number`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: pruning never removes state a pending proof requires
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: prune with a pending proof outstanding and assert the proof still verifies
