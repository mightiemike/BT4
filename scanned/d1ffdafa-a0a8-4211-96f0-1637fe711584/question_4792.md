# Q4792: genesis/system account preload via `initialize` (mod.rs)

## Question
Can an unprivileged attacker who sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space, controlling contract bytecode and calldata, drive `initialize` in `crates/evm/src/evm/system_contracts/mod.rs` so that the balances genesis installs and the balances the first proved root contains stop being equal, breaking the invariant that genesis state is exactly what the circuit starts from?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `initialize`
- Entrypoint: unprivileged party sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space
- Attacker controls: contract bytecode and calldata
- Exploit idea: genesis/system account preload - reach `initialize` from that entrypoint and force the divergence where the balances genesis installs and the balances the first proved root contains stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: genesis state is exactly what the circuit starts from
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: diff the genesis root against the circuit's initial root
