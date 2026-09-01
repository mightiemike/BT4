# Q0167: coinbase/basefee exposure via `create_txn_env` (call.rs)

## Question
Can an unprivileged attacker who sends a transaction that reverts after writing large state diffs, controlling calldata entropy, drive `create_txn_env` in `crates/evm/src/evm/call.rs` so that the block fields exposed to contracts and the fields the header commits stop being equal, breaking the invariant that EVM block context equals the sealed header?

## Target
- File/function: `crates/evm/src/evm/call.rs` -> `create_txn_env`
- Entrypoint: unprivileged party sends a transaction that reverts after writing large state diffs
- Attacker controls: calldata entropy
- Exploit idea: coinbase/basefee exposure - reach `create_txn_env` from that entrypoint and force the divergence where the block fields exposed to contracts and the fields the header commits stop being equal; the adjacent symbols in the same file that carry the value are `prepare_call_env`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: EVM block context equals the sealed header
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: read every block opcode from a contract and diff against the header
