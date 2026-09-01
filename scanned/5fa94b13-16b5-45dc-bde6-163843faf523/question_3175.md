# Q3175: stale-node pruning versus proof availability via `set_stale_nodes` (state_db.rs)

## Question
Can an unprivileged attacker who sends transactions that cause the same key to be written by two different modules in one block, controlling the index or key an attacker-supplied field derives, drive `set_stale_nodes` in `crates/sovereign-sdk/full-node/db/sov-db/src/state_db.rs` so that the nodes pruned and the nodes no proof will ever need stop being the same set, breaking the invariant that pruning never removes state a pending proof requires?

## Target
- File/function: `crates/sovereign-sdk/full-node/db/sov-db/src/state_db.rs` -> `set_stale_nodes`
- Entrypoint: unprivileged party sends transactions that cause the same key to be written by two different modules in one block
- Attacker controls: the index or key an attacker-supplied field derives
- Exploit idea: stale-node pruning versus proof availability - reach `set_stale_nodes` from that entrypoint and force the divergence where the nodes pruned and the nodes no proof will ever need stop being the same set; the adjacent symbols in the same file that carry the value are `StateDB`, `setup_schema_db`, `freeze`, `next_version`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: pruning never removes state a pending proof requires
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: prune with a pending proof outstanding and assert the proof still verifies
