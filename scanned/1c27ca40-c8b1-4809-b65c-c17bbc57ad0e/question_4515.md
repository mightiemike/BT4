# Q4515: receipt/log index skew via `apply_account_override` (mod.rs)

## Question
Can an unprivileged attacker who installs a filter and polls it across a block boundary, controlling poll timing across blocks, drive `apply_account_override` in `crates/evm/src/rpc_helpers/mod.rs` so that the log index in a returned receipt and the index the block's log ordering assigns stop being equal, breaking the invariant that log indices are stable for a canonical block?

## Target
- File/function: `crates/evm/src/rpc_helpers/mod.rs` -> `apply_account_override`
- Entrypoint: unprivileged party installs a filter and polls it across a block boundary
- Attacker controls: poll timing across blocks
- Exploit idea: receipt/log index skew - reach `apply_account_override` from that entrypoint and force the divergence where the log index in a returned receipt and the index the block's log ordering assigns stop being equal; the adjacent symbols in the same file that carry the value are `apply_state_overrides`, `apply_block_overrides`, `generate_eth_proof`, `generate_account_proof`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: log indices are stable for a canonical block
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: compare receipts against a full-block re-derivation
