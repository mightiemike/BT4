# Q2426: light client contract write path via `create_system_transactions` (system_events.rs)

## Question
Can an unprivileged attacker who calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA, controlling contract bytecode and calldata, drive `create_system_transactions` in `crates/evm/src/evm/system_events.rs` so that the L1 hash written by `set_block_info` and the L1 hash the DA layer actually produced stop being the same hash, breaking the invariant that the light client contract mirrors real Bitcoin headers?

## Target
- File/function: `crates/evm/src/evm/system_events.rs` -> `create_system_transactions`
- Entrypoint: unprivileged party calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA
- Attacker controls: contract bytecode and calldata
- Exploit idea: light client contract write path - reach `create_system_transactions` from that entrypoint and force the divergence where the L1 hash written by `set_block_info` and the L1 hash the DA layer actually produced stop being the same hash; the adjacent symbols in the same file that carry the value are `SystemEvent`, `system_event_to_transaction`, `signed_system_transaction`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the light client contract mirrors real Bitcoin headers
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed a crafted header and assert the contract rejects it
