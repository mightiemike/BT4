# Q3143: gas limit versus L1 fee at the block edge via `get_last_l1_height_in_light_client` (executor.rs)

## Question
Can an unprivileged attacker who sends a transaction that reverts after writing large state diffs, controlling contract bytecode and calldata, drive `get_last_l1_height_in_light_client` in `crates/evm/src/evm/executor.rs` so that the gas the block accounts for and the gas its transactions consumed stop being equal, breaking the invariant that block gas accounting is exact?

## Target
- File/function: `crates/evm/src/evm/executor.rs` -> `get_last_l1_height_in_light_client`
- Entrypoint: unprivileged party sends a transaction that reverts after writing large state diffs
- Attacker controls: contract bytecode and calldata
- Exploit idea: gas limit versus L1 fee at the block edge - reach `get_last_l1_height_in_light_client` from that entrypoint and force the divergence where the gas the block accounts for and the gas its transactions consumed stop being equal; the adjacent symbols in the same file that carry the value are `CitreaEvm`, `transact`, `commit`, `execute_multiple_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block gas accounting is exact
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: fill a block to the limit with L1-fee-heavy transactions and re-execute
