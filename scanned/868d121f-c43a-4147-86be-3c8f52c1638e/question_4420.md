# Q4420: filter surviving a prune boundary via `watch_and_clear_stale_filters` (filter.rs)

## Question
Can an unprivileged attacker who installs a filter and polls it across a block boundary, controlling filter ranges, topics and block tags, drive `watch_and_clear_stale_filters` in `crates/evm/src/rpc_helpers/filter.rs` so that the range a filter still answers for and the range the node can still prove stop being the same, breaking the invariant that answers are bounded by provable history?

## Target
- File/function: `crates/evm/src/rpc_helpers/filter.rs` -> `watch_and_clear_stale_filters`
- Entrypoint: unprivileged party installs a filter and polls it across a block boundary
- Attacker controls: filter ranges, topics and block tags
- Exploit idea: filter surviving a prune boundary - reach `watch_and_clear_stale_filters` from that entrypoint and force the divergence where the range a filter still answers for and the range the node can still prove stop being the same; the adjacent symbols in the same file that carry the value are `ActiveFilters`, `ActiveFilter`, `FilterKind`, `CitreaFilter`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: answers are bounded by provable history
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: poll a filter across the prune horizon and assert an explicit error
