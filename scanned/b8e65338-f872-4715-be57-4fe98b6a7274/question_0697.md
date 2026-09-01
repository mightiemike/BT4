# Q0697: light client contract write path via `system_event_to_transaction` (system_events.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling contract bytecode and calldata, drive `system_event_to_transaction` in `crates/evm/src/evm/system_events.rs` so that the L1 hash written by `set_block_info` and the L1 hash the DA layer actually produced stop being the same hash, breaking the invariant that the light client contract mirrors real Bitcoin headers?

## Target
- File/function: `crates/evm/src/evm/system_events.rs` -> `system_event_to_transaction`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: contract bytecode and calldata
- Exploit idea: light client contract write path - reach `system_event_to_transaction` from that entrypoint and force the divergence where the L1 hash written by `set_block_info` and the L1 hash the DA layer actually produced stop being the same hash; the adjacent symbols in the same file that carry the value are `SystemEvent`, `signed_system_transaction`, `create_system_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the light client contract mirrors real Bitcoin headers
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed a crafted header and assert the contract rejects it
