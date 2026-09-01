# Q5405: witness root exposure via `populate_set_block_info_event` (hooks.rs)

## Question
Can an unprivileged attacker who sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space, controlling contract bytecode and calldata, drive `populate_set_block_info_event` in `crates/evm/src/hooks.rs` so that the witness root a contract reads and the witness root of the referenced L1 block stop being equal, breaking the invariant that contract-visible L1 data equals verified L1 data?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `populate_set_block_info_event`
- Entrypoint: unprivileged party sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space
- Attacker controls: contract bytecode and calldata
- Exploit idea: witness root exposure - reach `populate_set_block_info_event` from that entrypoint and force the divergence where the witness root a contract reads and the witness root of the referenced L1 block stop being equal; the adjacent symbols in the same file that carry the value are `begin_l2_block_hook`, `end_l2_block_hook`, `finalize_hook`, `create_initial_system_events`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: contract-visible L1 data equals verified L1 data
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: query `get_witness_root_by_number` for an unset height and assert a defined failure
