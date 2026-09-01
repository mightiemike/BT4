# Q0777: bridge deposit reentered from user code via `create_system_transactions` (system_events.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling contract bytecode and calldata, drive `create_system_transactions` in `crates/evm/src/evm/system_events.rs` so that the deposit credit path a user contract can reach and the path reserved for system transactions stop being distinct, breaking the invariant that user code cannot re-enter deposit crediting?

## Target
- File/function: `crates/evm/src/evm/system_events.rs` -> `create_system_transactions`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: contract bytecode and calldata
- Exploit idea: bridge deposit reentered from user code - reach `create_system_transactions` from that entrypoint and force the divergence where the deposit credit path a user contract can reach and the path reserved for system transactions stop being distinct; the adjacent symbols in the same file that carry the value are `SystemEvent`, `system_event_to_transaction`, `signed_system_transaction`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: user code cannot re-enter deposit crediting
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: re-enter `deposit` from a user contract and assert rejection
