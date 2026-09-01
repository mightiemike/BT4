# Q2147: block hook ordering via `address` (mod.rs)

## Question
Can an unprivileged attacker who calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA, controlling contract bytecode and calldata, drive `address` in `crates/evm/src/evm/system_contracts/mod.rs` so that the state the deposit hook observes and the state the block header commits stop being the same state, breaking the invariant that hooks run against the state the block commits?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `address`
- Entrypoint: unprivileged party calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA
- Attacker controls: contract bytecode and calldata
- Exploit idea: block hook ordering - reach `address` from that entrypoint and force the divergence where the state the deposit hook observes and the state the block header commits stop being the same state; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: hooks run against the state the block commits
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: reorder hook execution and diff the resulting root
