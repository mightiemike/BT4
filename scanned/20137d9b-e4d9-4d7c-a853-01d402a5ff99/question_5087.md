# Q5087: system contract callable by users via `create_system_transactions` (system_events.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling the target system-contract address and selector, drive `create_system_transactions` in `crates/evm/src/evm/system_events.rs` so that the caller the system contract accepts and the system caller the protocol intends stop being the same party, breaking the invariant that only the system signer can drive `set_block_info` / `deposit`?

## Target
- File/function: `crates/evm/src/evm/system_events.rs` -> `create_system_transactions`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: the target system-contract address and selector
- Exploit idea: system contract callable by users - reach `create_system_transactions` from that entrypoint and force the divergence where the caller the system contract accepts and the system caller the protocol intends stop being the same party; the adjacent symbols in the same file that carry the value are `SystemEvent`, `system_event_to_transaction`, `signed_system_transaction`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only the system signer can drive `set_block_info` / `deposit`
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: call the light client and bridge setters from an EOA and assert rejection
