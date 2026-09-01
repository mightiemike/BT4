# Q5421: genesis/system account preload via `populate_set_block_info_event` (hooks.rs)

## Question
Can an unprivileged attacker who sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space, controlling value and gas, drive `populate_set_block_info_event` in `crates/evm/src/hooks.rs` so that the balances genesis installs and the balances the first proved root contains stop being equal, breaking the invariant that genesis state is exactly what the circuit starts from?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `populate_set_block_info_event`
- Entrypoint: unprivileged party sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space
- Attacker controls: value and gas
- Exploit idea: genesis/system account preload - reach `populate_set_block_info_event` from that entrypoint and force the divergence where the balances genesis installs and the balances the first proved root contains stop being equal; the adjacent symbols in the same file that carry the value are `begin_l2_block_hook`, `end_l2_block_hook`, `finalize_hook`, `create_initial_system_events`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: genesis state is exactly what the circuit starts from
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: diff the genesis root against the circuit's initial root
