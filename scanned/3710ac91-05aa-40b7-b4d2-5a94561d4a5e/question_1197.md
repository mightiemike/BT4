# Q1197: system contract callable by users via `populate_set_block_info_event` (hooks.rs)

## Question
Can an unprivileged attacker who sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space, controlling contract bytecode and calldata, drive `populate_set_block_info_event` in `crates/evm/src/hooks.rs` so that the caller the system contract accepts and the system caller the protocol intends stop being the same party, breaking the invariant that only the system signer can drive `set_block_info` / `deposit`?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `populate_set_block_info_event`
- Entrypoint: unprivileged party sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space
- Attacker controls: contract bytecode and calldata
- Exploit idea: system contract callable by users - reach `populate_set_block_info_event` from that entrypoint and force the divergence where the caller the system contract accepts and the system caller the protocol intends stop being the same party; the adjacent symbols in the same file that carry the value are `begin_l2_block_hook`, `end_l2_block_hook`, `finalize_hook`, `create_initial_system_events`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only the system signer can drive `set_block_info` / `deposit`
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: call the light client and bridge setters from an EOA and assert rejection
