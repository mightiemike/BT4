# Q3707: nonce handling for system transactions via `init` (mod.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling value and gas, drive `init` in `crates/evm/src/evm/system_contracts/mod.rs` so that the nonce sequence system transactions consume and the sequence the account tracks stop being the same, breaking the invariant that system transactions never desynchronise the system account?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `init`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: value and gas
- Exploit idea: nonce handling for system transactions - reach `init` from that entrypoint and force the divergence where the nonce sequence system transactions consume and the sequence the account tracks stop being the same; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: system transactions never desynchronise the system account
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: produce many deposits in one block and assert every one executes
