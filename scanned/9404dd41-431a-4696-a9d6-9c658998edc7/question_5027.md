# Q5027: WCBTC wrap/unwrap conservation via `system_event_to_transaction` (system_events.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling the target system-contract address and selector, drive `system_event_to_transaction` in `crates/evm/src/evm/system_events.rs` so that the cBTC locked by wrapping and the cBTC released by unwrapping stop being equal, breaking the invariant that wrapped supply equals locked supply?

## Target
- File/function: `crates/evm/src/evm/system_events.rs` -> `system_event_to_transaction`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: the target system-contract address and selector
- Exploit idea: WCBTC wrap/unwrap conservation - reach `system_event_to_transaction` from that entrypoint and force the divergence where the cBTC locked by wrapping and the cBTC released by unwrapping stop being equal; the adjacent symbols in the same file that carry the value are `SystemEvent`, `signed_system_transaction`, `create_system_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: wrapped supply equals locked supply
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: wrap and unwrap adversarially and assert conservation
