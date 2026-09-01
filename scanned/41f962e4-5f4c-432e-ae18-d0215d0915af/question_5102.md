# Q5102: witness root exposure via `system_event_to_transaction` (system_events.rs)

## Question
Can an unprivileged attacker who calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA, controlling the target system-contract address and selector, drive `system_event_to_transaction` in `crates/evm/src/evm/system_events.rs` so that the witness root a contract reads and the witness root of the referenced L1 block stop being equal, breaking the invariant that contract-visible L1 data equals verified L1 data?

## Target
- File/function: `crates/evm/src/evm/system_events.rs` -> `system_event_to_transaction`
- Entrypoint: unprivileged party calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA
- Attacker controls: the target system-contract address and selector
- Exploit idea: witness root exposure - reach `system_event_to_transaction` from that entrypoint and force the divergence where the witness root a contract reads and the witness root of the referenced L1 block stop being equal; the adjacent symbols in the same file that carry the value are `SystemEvent`, `signed_system_transaction`, `create_system_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: contract-visible L1 data equals verified L1 data
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: query `get_witness_root_by_number` for an unset height and assert a defined failure
