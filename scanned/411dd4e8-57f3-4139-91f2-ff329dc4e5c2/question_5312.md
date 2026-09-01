# Q5312: block hook ordering via `end_l2_block_hook` (hooks.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling contract bytecode and calldata, drive `end_l2_block_hook` in `crates/evm/src/hooks.rs` so that the state the deposit hook observes and the state the block header commits stop being the same state, breaking the invariant that hooks run against the state the block commits?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `end_l2_block_hook`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: contract bytecode and calldata
- Exploit idea: block hook ordering - reach `end_l2_block_hook` from that entrypoint and force the divergence where the state the deposit hook observes and the state the block header commits stop being the same state; the adjacent symbols in the same file that carry the value are `begin_l2_block_hook`, `finalize_hook`, `create_initial_system_events`, `populate_set_block_info_event`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: hooks run against the state the block commits
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: reorder hook execution and diff the resulting root
