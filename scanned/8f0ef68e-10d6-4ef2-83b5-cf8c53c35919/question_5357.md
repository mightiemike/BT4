# Q5357: vault withdrawal accounting via `populate_set_block_info_event` (hooks.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling the target system-contract address and selector, drive `populate_set_block_info_event` in `crates/evm/src/hooks.rs` so that the balance a fee vault reports and the fees actually routed to it stop being equal, breaking the invariant that vault balances equal routed fees?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `populate_set_block_info_event`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: the target system-contract address and selector
- Exploit idea: vault withdrawal accounting - reach `populate_set_block_info_event` from that entrypoint and force the divergence where the balance a fee vault reports and the fees actually routed to it stop being equal; the adjacent symbols in the same file that carry the value are `begin_l2_block_hook`, `end_l2_block_hook`, `finalize_hook`, `create_initial_system_events`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: vault balances equal routed fees
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: sum routed fees across a block and compare
