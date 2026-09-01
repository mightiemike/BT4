# Q2097: fee vault accounting via `init` (mod.rs)

## Question
Can an unprivileged attacker who sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space, controlling value and gas, drive `init` in `crates/evm/src/evm/system_contracts/mod.rs` so that the value deducted from the sender and the value credited to the base-fee/L1-fee/priority-fee vaults stop being equal, breaking the invariant that fees are conserved between payer and vaults?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `init`
- Entrypoint: unprivileged party sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space
- Attacker controls: value and gas
- Exploit idea: fee vault accounting - reach `init` from that entrypoint and force the divergence where the value deducted from the sender and the value credited to the base-fee/L1-fee/priority-fee vaults stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fees are conserved between payer and vaults
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: sum vault deltas against sender deltas over an adversarial block
