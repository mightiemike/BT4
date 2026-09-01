# Q5597: coinbase/basefee exposure via `cancun` (handler.rs)

## Question
Can an unprivileged attacker who sends a transaction that reverts after writing large state diffs, controlling value, gas and access list, drive `cancun` in `crates/evm/src/evm/handler.rs` so that the block fields exposed to contracts and the fields the header commits stop being equal, breaking the invariant that EVM block context equals the sealed header?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `cancun`
- Entrypoint: unprivileged party sends a transaction that reverts after writing large state diffs
- Attacker controls: value, gas and access list
- Exploit idea: coinbase/basefee exposure - reach `cancun` from that entrypoint and force the divergence where the block fields exposed to contracts and the fields the header commits stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: EVM block context equals the sealed header
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: read every block opcode from a contract and diff against the header
