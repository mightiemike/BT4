# Q5195: receipt/log index skew via `apply_block_overrides` (mod.rs)

## Question
Can an unprivileged attacker who calls `eth_getLogs` with a filter range and topic set of their choosing, controlling the log-emitting contract's bytecode, drive `apply_block_overrides` in `crates/evm/src/rpc_helpers/mod.rs` so that the log index in a returned receipt and the index the block's log ordering assigns stop being equal, breaking the invariant that log indices are stable for a canonical block?

## Target
- File/function: `crates/evm/src/rpc_helpers/mod.rs` -> `apply_block_overrides`
- Entrypoint: unprivileged party calls `eth_getLogs` with a filter range and topic set of their choosing
- Attacker controls: the log-emitting contract's bytecode
- Exploit idea: receipt/log index skew - reach `apply_block_overrides` from that entrypoint and force the divergence where the log index in a returned receipt and the index the block's log ordering assigns stop being equal; the adjacent symbols in the same file that carry the value are `apply_state_overrides`, `apply_account_override`, `generate_eth_proof`, `generate_account_proof`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: log indices are stable for a canonical block
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: compare receipts against a full-block re-derivation
