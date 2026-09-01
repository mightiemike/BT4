# Q4037: system-caller impersonation via `is_system_caller` (handler.rs)

## Question
Can an unprivileged attacker who chains nested frames that touch balances, gas refunds and access lists in one transaction, controlling calldata entropy, drive `is_system_caller` in `crates/evm/src/evm/handler.rs` so that the account `is_system_caller` treats as privileged and `SYSTEM_SIGNER` stop being the same account, breaking the invariant that only genuine system transactions skip fee and access rules?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `is_system_caller`
- Entrypoint: unprivileged party chains nested frames that touch balances, gas refunds and access lists in one transaction
- Attacker controls: calldata entropy
- Exploit idea: system-caller impersonation - reach `is_system_caller` from that entrypoint and force the divergence where the account `is_system_caller` treats as privileged and `SYSTEM_SIGNER` stop being the same account; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only genuine system transactions skip fee and access rules
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: send an EOA transaction shaped like a system transaction and assert fees are charged
