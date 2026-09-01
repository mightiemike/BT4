# Q3391: receipt/log index skew via `watch_and_clear_stale_filters` (filter.rs)

## Question
Can an unprivileged attacker who calls `eth_getLogs` with a filter range and topic set of their choosing, controlling poll timing across blocks, drive `watch_and_clear_stale_filters` in `crates/evm/src/rpc_helpers/filter.rs` so that the log index in a returned receipt and the index the block's log ordering assigns stop being equal, breaking the invariant that log indices are stable for a canonical block?

## Target
- File/function: `crates/evm/src/rpc_helpers/filter.rs` -> `watch_and_clear_stale_filters`
- Entrypoint: unprivileged party calls `eth_getLogs` with a filter range and topic set of their choosing
- Attacker controls: poll timing across blocks
- Exploit idea: receipt/log index skew - reach `watch_and_clear_stale_filters` from that entrypoint and force the divergence where the log index in a returned receipt and the index the block's log ordering assigns stop being equal; the adjacent symbols in the same file that carry the value are `ActiveFilters`, `ActiveFilter`, `FilterKind`, `CitreaFilter`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: log indices are stable for a canonical block
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: compare receipts against a full-block re-derivation
