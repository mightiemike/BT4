# Q2367: fee vault accounting via `balance_of` (mod.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling value and gas, drive `balance_of` in `crates/evm/src/evm/system_contracts/mod.rs` so that the value deducted from the sender and the value credited to the base-fee/L1-fee/priority-fee vaults stop being equal, breaking the invariant that fees are conserved between payer and vaults?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `balance_of`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: value and gas
- Exploit idea: fee vault accounting - reach `balance_of` from that entrypoint and force the divergence where the value deducted from the sender and the value credited to the base-fee/L1-fee/priority-fee vaults stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fees are conserved between payer and vaults
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: sum vault deltas against sender deltas over an adversarial block
