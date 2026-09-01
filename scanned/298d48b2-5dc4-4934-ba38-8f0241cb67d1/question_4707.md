# Q4707: system event ordering versus user txs via `get_block_hash` (mod.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling value and gas, drive `get_block_hash` in `crates/evm/src/evm/system_contracts/mod.rs` so that the position system events occupy in the block and the position the STF assumes stop being the same, breaking the invariant that system events are first-class and fixed in order?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `get_block_hash`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: value and gas
- Exploit idea: system event ordering versus user txs - reach `get_block_hash` from that entrypoint and force the divergence where the position system events occupy in the block and the position the STF assumes stop being the same; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: system events are first-class and fixed in order
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: interleave and re-execute the block
