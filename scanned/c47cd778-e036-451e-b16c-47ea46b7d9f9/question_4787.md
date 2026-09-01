# Q4787: coinbase/basefee exposure via `get_last_l1_height_in_light_client` (executor.rs)

## Question
Can an unprivileged attacker who deploys a contract and calls it in the same L2 block, controlling calldata entropy, drive `get_last_l1_height_in_light_client` in `crates/evm/src/evm/executor.rs` so that the block fields exposed to contracts and the fields the header commits stop being equal, breaking the invariant that EVM block context equals the sealed header?

## Target
- File/function: `crates/evm/src/evm/executor.rs` -> `get_last_l1_height_in_light_client`
- Entrypoint: unprivileged party deploys a contract and calls it in the same L2 block
- Attacker controls: calldata entropy
- Exploit idea: coinbase/basefee exposure - reach `get_last_l1_height_in_light_client` from that entrypoint and force the divergence where the block fields exposed to contracts and the fields the header commits stop being equal; the adjacent symbols in the same file that carry the value are `CitreaEvm`, `transact`, `commit`, `execute_multiple_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: EVM block context equals the sealed header
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: read every block opcode from a contract and diff against the header
