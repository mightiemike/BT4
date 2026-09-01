# Q4192: system contract callable by users via `finalize_hook` (hooks.rs)

## Question
Can an unprivileged attacker who calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA, controlling the target system-contract address and selector, drive `finalize_hook` in `crates/evm/src/hooks.rs` so that the caller the system contract accepts and the system caller the protocol intends stop being the same party, breaking the invariant that only the system signer can drive `set_block_info` / `deposit`?

## Target
- File/function: `crates/evm/src/hooks.rs` -> `finalize_hook`
- Entrypoint: unprivileged party calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA
- Attacker controls: the target system-contract address and selector
- Exploit idea: system contract callable by users - reach `finalize_hook` from that entrypoint and force the divergence where the caller the system contract accepts and the system caller the protocol intends stop being the same party; the adjacent symbols in the same file that carry the value are `begin_l2_block_hook`, `end_l2_block_hook`, `create_initial_system_events`, `populate_set_block_info_event`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only the system signer can drive `set_block_info` / `deposit`
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: call the light client and bridge setters from an EOA and assert rejection
