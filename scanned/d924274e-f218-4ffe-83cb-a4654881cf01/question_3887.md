# Q3887: fee charge underflow at the balance edge via `get_last_l1_height_and_hash_in_light_client` (executor.rs)

## Question
Can an unprivileged attacker who sends a transaction that reverts after writing large state diffs, controlling value, gas and access list, drive `get_last_l1_height_and_hash_in_light_client` in `crates/evm/src/evm/executor.rs` so that the balance debited for gas plus L1 fee and the balance the account actually holds stop being reconcilable, breaking the invariant that fee charging never underflows or silently skips?

## Target
- File/function: `crates/evm/src/evm/executor.rs` -> `get_last_l1_height_and_hash_in_light_client`
- Entrypoint: unprivileged party sends a transaction that reverts after writing large state diffs
- Attacker controls: value, gas and access list
- Exploit idea: fee charge underflow at the balance edge - reach `get_last_l1_height_and_hash_in_light_client` from that entrypoint and force the divergence where the balance debited for gas plus L1 fee and the balance the account actually holds stop being reconcilable; the adjacent symbols in the same file that carry the value are `CitreaEvm`, `transact`, `commit`, `execute_multiple_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fee charging never underflows or silently skips
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: execute at the exact balance boundary and assert exact accounting
