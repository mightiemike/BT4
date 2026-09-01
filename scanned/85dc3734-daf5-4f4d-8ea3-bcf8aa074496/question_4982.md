# Q4982: block hook ordering via `owner` (mod.rs)

## Question
Can an unprivileged attacker who sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space, controlling the target system-contract address and selector, drive `owner` in `crates/evm/src/evm/system_contracts/mod.rs` so that the state the deposit hook observes and the state the block header commits stop being the same state, breaking the invariant that hooks run against the state the block commits?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `owner`
- Entrypoint: unprivileged party sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space
- Attacker controls: the target system-contract address and selector
- Exploit idea: block hook ordering - reach `owner` from that entrypoint and force the divergence where the state the deposit hook observes and the state the block header commits stop being the same state; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: hooks run against the state the block commits
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: reorder hook execution and diff the resulting root
