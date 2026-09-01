# Q3862: system event ordering versus user txs via `system_event_to_transaction` (system_events.rs)

## Question
Can an unprivileged attacker who sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space, controlling the target system-contract address and selector, drive `system_event_to_transaction` in `crates/evm/src/evm/system_events.rs` so that the position system events occupy in the block and the position the STF assumes stop being the same, breaking the invariant that system events are first-class and fixed in order?

## Target
- File/function: `crates/evm/src/evm/system_events.rs` -> `system_event_to_transaction`
- Entrypoint: unprivileged party sends a transaction crafted to collide with the `SYSTEM_SIGNER` account's nonce space
- Attacker controls: the target system-contract address and selector
- Exploit idea: system event ordering versus user txs - reach `system_event_to_transaction` from that entrypoint and force the divergence where the position system events occupy in the block and the position the STF assumes stop being the same; the adjacent symbols in the same file that carry the value are `SystemEvent`, `signed_system_transaction`, `create_system_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: system events are first-class and fixed in order
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: interleave and re-execute the block
