# Q0877: gas limit versus L1 fee at the block edge via `is_system_caller` (handler.rs)

## Question
Can an unprivileged attacker who sends a transaction that reverts after writing large state diffs, controlling value, gas and access list, drive `is_system_caller` in `crates/evm/src/evm/handler.rs` so that the gas the block accounts for and the gas its transactions consumed stop being equal, breaking the invariant that block gas accounting is exact?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `is_system_caller`
- Entrypoint: unprivileged party sends a transaction that reverts after writing large state diffs
- Attacker controls: value, gas and access list
- Exploit idea: gas limit versus L1 fee at the block edge - reach `is_system_caller` from that entrypoint and force the divergence where the gas the block accounts for and the gas its transactions consumed stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block gas accounting is exact
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: fill a block to the limit with L1-fee-heavy transactions and re-execute
