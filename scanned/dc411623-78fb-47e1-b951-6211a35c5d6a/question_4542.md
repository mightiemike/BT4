# Q4542: block hook ordering via `get_system_caller` (mod.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling value and gas, drive `get_system_caller` in `crates/evm/src/evm/system_contracts/mod.rs` so that the state the deposit hook observes and the state the block header commits stop being the same state, breaking the invariant that hooks run against the state the block commits?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `get_system_caller`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: value and gas
- Exploit idea: block hook ordering - reach `get_system_caller` from that entrypoint and force the divergence where the state the deposit hook observes and the state the block header commits stop being the same state; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: hooks run against the state the block commits
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: reorder hook execution and diff the resulting root
