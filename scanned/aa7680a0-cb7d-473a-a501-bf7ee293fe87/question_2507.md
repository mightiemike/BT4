# Q2507: WCBTC wrap/unwrap conservation via `begin_l2_block_hook` (hooks.rs)

## Question
Can an unprivileged attacker who sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space, controlling value and gas, drive `begin_l2_block_hook` in `crates/evm/src/hooks.rs` so that the cBTC locked by wrapping and the cBTC released by unwrapping stop being equal, breaking the invariant that wrapped supply equals locked supply?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `begin_l2_block_hook`
- Entrypoint: unprivileged party sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space
- Attacker controls: value and gas
- Exploit idea: WCBTC wrap/unwrap conservation - reach `begin_l2_block_hook` from that entrypoint and force the divergence where the cBTC locked by wrapping and the cBTC released by unwrapping stop being equal; the adjacent symbols in the same file that carry the value are `end_l2_block_hook`, `finalize_hook`, `create_initial_system_events`, `populate_set_block_info_event`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: wrapped supply equals locked supply
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: wrap and unwrap adversarially and assert conservation
