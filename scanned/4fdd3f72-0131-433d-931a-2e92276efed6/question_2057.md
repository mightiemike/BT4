# Q2057: system-caller impersonation via `sign_authorization` (mod.rs)

## Question
Can an unprivileged attacker who sends a transaction that reverts after writing large state diffs, controlling calldata entropy, drive `sign_authorization` in `crates/evm/src/signer/mod.rs` so that the account `is_system_caller` treats as privileged and `SYSTEM_SIGNER` stop being the same account, breaking the invariant that only genuine system transactions skip fee and access rules?

## Target
- File/function: `crates/evm/src/signer/mod.rs` -> `sign_authorization`
- Entrypoint: unprivileged party sends a transaction that reverts after writing large state diffs
- Attacker controls: calldata entropy
- Exploit idea: system-caller impersonation - reach `sign_authorization` from that entrypoint and force the divergence where the account `is_system_caller` treats as privileged and `SYSTEM_SIGNER` stop being the same account; the adjacent symbols in the same file that carry the value are `DevSigner`, `sign_transaction`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only genuine system transactions skip fee and access rules
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: send an EOA transaction shaped like a system transaction and assert fees are charged
