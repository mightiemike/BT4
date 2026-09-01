# Q4627: system-caller impersonation via `verify_system_tx` (executor.rs)

## Question
Can an unprivileged attacker who sends a transaction that reverts after writing large state diffs, controlling revert timing inside the frame, drive `verify_system_tx` in `crates/evm/src/evm/executor.rs` so that the account `is_system_caller` treats as privileged and `SYSTEM_SIGNER` stop being the same account, breaking the invariant that only genuine system transactions skip fee and access rules?

## Target
- File/function: `crates/evm/src/evm/executor.rs` -> `verify_system_tx`
- Entrypoint: unprivileged party sends a transaction that reverts after writing large state diffs
- Attacker controls: revert timing inside the frame
- Exploit idea: system-caller impersonation - reach `verify_system_tx` from that entrypoint and force the divergence where the account `is_system_caller` treats as privileged and `SYSTEM_SIGNER` stop being the same account; the adjacent symbols in the same file that carry the value are `CitreaEvm`, `transact`, `commit`, `execute_multiple_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only genuine system transactions skip fee and access rules
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: send an EOA transaction shaped like a system transaction and assert fees are charged
