# Q3231: stale-node pruning versus proof availability via `create_storage_for_next_l2_height` (lib.rs)

## Question
Can an unprivileged attacker who inscribes data that lands in a persisted table keyed by an attacker-influenced index, controlling the index or key an attacker-supplied field derives, drive `create_storage_for_next_l2_height` in `crates/sovereign-sdk/full-node/sov-prover-storage-manager/src/lib.rs` so that the nodes pruned and the nodes no proof will ever need stop being the same set, breaking the invariant that pruning never removes state a pending proof requires?

## Target
- File/function: `crates/sovereign-sdk/full-node/sov-prover-storage-manager/src/lib.rs` -> `create_storage_for_next_l2_height`
- Entrypoint: unprivileged party inscribes data that lands in a persisted table keyed by an attacker-influenced index
- Attacker controls: the index or key an attacker-supplied field derives
- Exploit idea: stale-node pruning versus proof availability - reach `create_storage_for_next_l2_height` from that entrypoint and force the divergence where the nodes pruned and the nodes no proof will ever need stop being the same set; the adjacent symbols in the same file that carry the value are `ProverStorageManager`, `with_db_handles`, `create_storage_for_l2_height`, `create_final_view_storage`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: pruning never removes state a pending proof requires
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: prune with a pending proof outstanding and assert the proof still verifies
