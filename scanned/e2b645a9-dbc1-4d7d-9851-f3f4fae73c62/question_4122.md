# Q4122: WCBTC wrap/unwrap conservation via `create_initial_system_events` (hooks.rs)

## Question
Can an unprivileged attacker who calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA, controlling value and gas, drive `create_initial_system_events` in `crates/evm/src/hooks.rs` so that the cBTC locked by wrapping and the cBTC released by unwrapping stop being equal, breaking the invariant that wrapped supply equals locked supply?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `create_initial_system_events`
- Entrypoint: unprivileged party calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA
- Attacker controls: value and gas
- Exploit idea: WCBTC wrap/unwrap conservation - reach `create_initial_system_events` from that entrypoint and force the divergence where the cBTC locked by wrapping and the cBTC released by unwrapping stop being equal; the adjacent symbols in the same file that carry the value are `begin_l2_block_hook`, `end_l2_block_hook`, `finalize_hook`, `populate_set_block_info_event`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: wrapped supply equals locked supply
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: wrap and unwrap adversarially and assert conservation
