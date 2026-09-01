# Q4340: ledger RPC index bounds via `get_head_l2_block_height` (rpc.rs)

## Question
Can an unprivileged attacker who asks a node for a proof or commitment it has stored but not yet verified, controlling range and index parameters, drive `get_head_l2_block_height` in `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` so that the range the ledger RPC iterates and the range the caller requested stop being the same range, breaking the invariant that range queries never read beyond the requested window?

## Target
- File/function: `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` -> `get_head_l2_block_height`
- Entrypoint: unprivileged party asks a node for a proof or commitment it has stored but not yet verified
- Attacker controls: range and index parameters
- Exploit idea: ledger RPC index bounds - reach `get_head_l2_block_height` from that entrypoint and force the divergence where the range the ledger RPC iterates and the range the caller requested stop being the same range; the adjacent symbols in the same file that carry the value are `check_if_l2_block_pruned`, `get_l2_block`, `get_l2_block_by_hash`, `get_l2_block_by_number`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: range queries never read beyond the requested window
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: call with reversed/oversized ranges and assert bounded output
