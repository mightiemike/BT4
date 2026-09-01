# Q3276: vault withdrawal accounting via `begin_l2_block_hook` (hooks.rs)

## Question
Can an unprivileged attacker who calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA, controlling the target system-contract address and selector, drive `begin_l2_block_hook` in `crates/evm/src/hooks.rs` so that the balance a fee vault reports and the fees actually routed to it stop being equal, breaking the invariant that vault balances equal routed fees?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `begin_l2_block_hook`
- Entrypoint: unprivileged party calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA
- Attacker controls: the target system-contract address and selector
- Exploit idea: vault withdrawal accounting - reach `begin_l2_block_hook` from that entrypoint and force the divergence where the balance a fee vault reports and the fees actually routed to it stop being equal; the adjacent symbols in the same file that carry the value are `end_l2_block_hook`, `finalize_hook`, `create_initial_system_events`, `populate_set_block_info_event`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: vault balances equal routed fees
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: sum routed fees across a block and compare
