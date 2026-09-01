# Q5697: system-caller impersonation via `change_balance` (handler.rs)

## Question
Can an unprivileged attacker who deploys a contract and calls it in the same L2 block, controlling value, gas and access list, drive `change_balance` in `crates/evm/src/evm/handler.rs` so that the account `is_system_caller` treats as privileged and `SYSTEM_SIGNER` stop being the same account, breaking the invariant that only genuine system transactions skip fee and access rules?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `change_balance`
- Entrypoint: unprivileged party deploys a contract and calls it in the same L2 block
- Attacker controls: value, gas and access list
- Exploit idea: system-caller impersonation - reach `change_balance` from that entrypoint and force the divergence where the account `is_system_caller` treats as privileged and `SYSTEM_SIGNER` stop being the same account; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only genuine system transactions skip fee and access rules
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: send an EOA transaction shaped like a system transaction and assert fees are charged
