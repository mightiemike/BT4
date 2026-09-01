# Q5425: nonce handling for system transactions via `populate_deposit_system_events` (hooks.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling the target system-contract address and selector, drive `populate_deposit_system_events` in `crates/evm/src/hooks.rs` so that the nonce sequence system transactions consume and the sequence the account tracks stop being the same, breaking the invariant that system transactions never desynchronise the system account?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `populate_deposit_system_events`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: the target system-contract address and selector
- Exploit idea: nonce handling for system transactions - reach `populate_deposit_system_events` from that entrypoint and force the divergence where the nonce sequence system transactions consume and the sequence the account tracks stop being the same; the adjacent symbols in the same file that carry the value are `begin_l2_block_hook`, `end_l2_block_hook`, `finalize_hook`, `create_initial_system_events`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: system transactions never desynchronise the system account
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: produce many deposits in one block and assert every one executes
