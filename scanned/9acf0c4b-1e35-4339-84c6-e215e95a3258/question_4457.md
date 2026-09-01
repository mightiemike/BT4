# Q4457: system-caller impersonation via `decrease_caller_balance` (handler.rs)

## Question
Can an unprivileged attacker who chains nested frames that touch balances, gas refunds and access lists in one transaction, controlling calldata entropy, drive `decrease_caller_balance` in `crates/evm/src/evm/handler.rs` so that the account `is_system_caller` treats as privileged and `SYSTEM_SIGNER` stop being the same account, breaking the invariant that only genuine system transactions skip fee and access rules?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `decrease_caller_balance`
- Entrypoint: unprivileged party chains nested frames that touch balances, gas refunds and access lists in one transaction
- Attacker controls: calldata entropy
- Exploit idea: system-caller impersonation - reach `decrease_caller_balance` from that entrypoint and force the divergence where the account `is_system_caller` treats as privileged and `SYSTEM_SIGNER` stop being the same account; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only genuine system transactions skip fee and access rules
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: send an EOA transaction shaped like a system transaction and assert fees are charged
