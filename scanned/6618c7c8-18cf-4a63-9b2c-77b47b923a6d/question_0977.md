# Q0977: witness root exposure via `begin_l2_block_hook` (hooks.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling contract bytecode and calldata, drive `begin_l2_block_hook` in `crates/evm/src/hooks.rs` so that the witness root a contract reads and the witness root of the referenced L1 block stop being equal, breaking the invariant that contract-visible L1 data equals verified L1 data?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `begin_l2_block_hook`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: contract bytecode and calldata
- Exploit idea: witness root exposure - reach `begin_l2_block_hook` from that entrypoint and force the divergence where the witness root a contract reads and the witness root of the referenced L1 block stop being equal; the adjacent symbols in the same file that carry the value are `end_l2_block_hook`, `finalize_hook`, `create_initial_system_events`, `populate_set_block_info_event`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: contract-visible L1 data equals verified L1 data
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: query `get_witness_root_by_number` for an unset height and assert a defined failure
