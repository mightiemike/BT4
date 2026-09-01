# Q5401: light client contract write path via `create_initial_system_events` (hooks.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling value and gas, drive `create_initial_system_events` in `crates/evm/src/hooks.rs` so that the L1 hash written by `set_block_info` and the L1 hash the DA layer actually produced stop being the same hash, breaking the invariant that the light client contract mirrors real Bitcoin headers?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `create_initial_system_events`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: value and gas
- Exploit idea: light client contract write path - reach `create_initial_system_events` from that entrypoint and force the divergence where the L1 hash written by `set_block_info` and the L1 hash the DA layer actually produced stop being the same hash; the adjacent symbols in the same file that carry the value are `begin_l2_block_hook`, `end_l2_block_hook`, `finalize_hook`, `populate_set_block_info_event`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the light client contract mirrors real Bitcoin headers
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed a crafted header and assert the contract rejects it
