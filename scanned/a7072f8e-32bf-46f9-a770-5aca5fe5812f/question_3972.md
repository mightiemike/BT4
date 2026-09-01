# Q3972: genesis/system account preload via `create_system_transactions` (system_events.rs)

## Question
Can an unprivileged attacker who calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA, controlling value and gas, drive `create_system_transactions` in `crates/evm/src/evm/system_events.rs` so that the balances genesis installs and the balances the first proved root contains stop being equal, breaking the invariant that genesis state is exactly what the circuit starts from?

## Target
- File/function: `crates/evm/src/evm/system_events.rs` -> `create_system_transactions`
- Entrypoint: unprivileged party calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA
- Attacker controls: value and gas
- Exploit idea: genesis/system account preload - reach `create_system_transactions` from that entrypoint and force the divergence where the balances genesis installs and the balances the first proved root contains stop being equal; the adjacent symbols in the same file that carry the value are `SystemEvent`, `system_event_to_transaction`, `signed_system_transaction`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: genesis state is exactly what the circuit starts from
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: diff the genesis root against the circuit's initial root
