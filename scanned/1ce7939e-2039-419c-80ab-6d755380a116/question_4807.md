# Q4807: WCBTC wrap/unwrap conservation via `initialize` (mod.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling contract bytecode and calldata, drive `initialize` in `crates/evm/src/evm/system_contracts/mod.rs` so that the cBTC locked by wrapping and the cBTC released by unwrapping stop being equal, breaking the invariant that wrapped supply equals locked supply?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `initialize`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: contract bytecode and calldata
- Exploit idea: WCBTC wrap/unwrap conservation - reach `initialize` from that entrypoint and force the divergence where the cBTC locked by wrapping and the cBTC released by unwrapping stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: wrapped supply equals locked supply
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: wrap and unwrap adversarially and assert conservation
