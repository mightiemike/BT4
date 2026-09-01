# Q4117: bridge deposit reentered from user code via `begin_l2_block_hook` (hooks.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling value and gas, drive `begin_l2_block_hook` in `crates/evm/src/hooks.rs` so that the deposit credit path a user contract can reach and the path reserved for system transactions stop being distinct, breaking the invariant that user code cannot re-enter deposit crediting?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `begin_l2_block_hook`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: value and gas
- Exploit idea: bridge deposit reentered from user code - reach `begin_l2_block_hook` from that entrypoint and force the divergence where the deposit credit path a user contract can reach and the path reserved for system transactions stop being distinct; the adjacent symbols in the same file that carry the value are `end_l2_block_hook`, `finalize_hook`, `create_initial_system_events`, `populate_set_block_info_event`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: user code cannot re-enter deposit crediting
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: re-enter `deposit` from a user contract and assert rejection
