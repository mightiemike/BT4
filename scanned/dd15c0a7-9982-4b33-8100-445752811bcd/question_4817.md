# Q4817: system event ordering versus user txs via `deposit` (mod.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling the target system-contract address and selector, drive `deposit` in `crates/evm/src/evm/system_contracts/mod.rs` so that the position system events occupy in the block and the position the STF assumes stop being the same, breaking the invariant that system events are first-class and fixed in order?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `deposit`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: the target system-contract address and selector
- Exploit idea: system event ordering versus user txs - reach `deposit` from that entrypoint and force the divergence where the position system events occupy in the block and the position the STF assumes stop being the same; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: system events are first-class and fixed in order
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: interleave and re-execute the block
