# Q3902: system-caller impersonation via `get_last_l1_height_in_light_client` (executor.rs)

## Question
Can an unprivileged attacker who sends a transaction that reverts after writing large state diffs, controlling value, gas and access list, drive `get_last_l1_height_in_light_client` in `crates/evm/src/evm/executor.rs` so that the account `is_system_caller` treats as privileged and `SYSTEM_SIGNER` stop being the same account, breaking the invariant that only genuine system transactions skip fee and access rules?

## Target
- File/function: `crates/evm/src/evm/executor.rs` -> `get_last_l1_height_in_light_client`
- Entrypoint: unprivileged party sends a transaction that reverts after writing large state diffs
- Attacker controls: value, gas and access list
- Exploit idea: system-caller impersonation - reach `get_last_l1_height_in_light_client` from that entrypoint and force the divergence where the account `is_system_caller` treats as privileged and `SYSTEM_SIGNER` stop being the same account; the adjacent symbols in the same file that carry the value are `CitreaEvm`, `transact`, `commit`, `execute_multiple_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only genuine system transactions skip fee and access rules
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: send an EOA transaction shaped like a system transaction and assert fees are charged
