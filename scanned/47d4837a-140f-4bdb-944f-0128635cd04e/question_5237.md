# Q5237: nonce handling for system transactions via `finalize_hook` (hooks.rs)

## Question
Can an unprivileged attacker who calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA, controlling contract bytecode and calldata, drive `finalize_hook` in `crates/evm/src/hooks.rs` so that the nonce sequence system transactions consume and the sequence the account tracks stop being the same, breaking the invariant that system transactions never desynchronise the system account?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `finalize_hook`
- Entrypoint: unprivileged party calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA
- Attacker controls: contract bytecode and calldata
- Exploit idea: nonce handling for system transactions - reach `finalize_hook` from that entrypoint and force the divergence where the nonce sequence system transactions consume and the sequence the account tracks stop being the same; the adjacent symbols in the same file that carry the value are `begin_l2_block_hook`, `end_l2_block_hook`, `create_initial_system_events`, `populate_set_block_info_event`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: system transactions never desynchronise the system account
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: produce many deposits in one block and assert every one executes
