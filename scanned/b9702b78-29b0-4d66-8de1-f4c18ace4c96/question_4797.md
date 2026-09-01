# Q4797: nonce handling for system transactions via `owner` (mod.rs)

## Question
Can an unprivileged attacker who calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA, controlling contract bytecode and calldata, drive `owner` in `crates/evm/src/evm/system_contracts/mod.rs` so that the nonce sequence system transactions consume and the sequence the account tracks stop being the same, breaking the invariant that system transactions never desynchronise the system account?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `owner`
- Entrypoint: unprivileged party calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA
- Attacker controls: contract bytecode and calldata
- Exploit idea: nonce handling for system transactions - reach `owner` from that entrypoint and force the divergence where the nonce sequence system transactions consume and the sequence the account tracks stop being the same; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: system transactions never desynchronise the system account
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: produce many deposits in one block and assert every one executes
