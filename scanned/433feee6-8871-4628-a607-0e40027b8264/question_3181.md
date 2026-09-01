# Q3181: log filter range inversion via `get_max_logs_per_response` (filter.rs)

## Question
Can an unprivileged attacker who installs a filter and polls it across a block boundary, controlling the log-emitting contract's bytecode, drive `get_max_logs_per_response` in `crates/evm/src/rpc_helpers/filter.rs` so that the log set the filter returns and the log set the canonical chain contains for that range stop being the same set, breaking the invariant that an RPC log answer is a subset of the proved chain's logs?

## Target
- File/function: `crates/evm/src/rpc_helpers/filter.rs` -> `get_max_logs_per_response`
- Entrypoint: unprivileged party installs a filter and polls it across a block boundary
- Attacker controls: the log-emitting contract's bytecode
- Exploit idea: log filter range inversion - reach `get_max_logs_per_response` from that entrypoint and force the divergence where the log set the filter returns and the log set the canonical chain contains for that range stop being the same set; the adjacent symbols in the same file that carry the value are `ActiveFilters`, `ActiveFilter`, `FilterKind`, `CitreaFilter`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: an RPC log answer is a subset of the proved chain's logs
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: compare filter output against a direct chain walk for adversarial ranges
