# Q1057: WCBTC wrap/unwrap conservation via `finalize_hook` (hooks.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling contract bytecode and calldata, drive `finalize_hook` in `crates/evm/src/hooks.rs` so that the cBTC locked by wrapping and the cBTC released by unwrapping stop being equal, breaking the invariant that wrapped supply equals locked supply?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `finalize_hook`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: contract bytecode and calldata
- Exploit idea: WCBTC wrap/unwrap conservation - reach `finalize_hook` from that entrypoint and force the divergence where the cBTC locked by wrapping and the cBTC released by unwrapping stop being equal; the adjacent symbols in the same file that carry the value are `begin_l2_block_hook`, `end_l2_block_hook`, `create_initial_system_events`, `populate_set_block_info_event`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: wrapped supply equals locked supply
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: wrap and unwrap adversarially and assert conservation
