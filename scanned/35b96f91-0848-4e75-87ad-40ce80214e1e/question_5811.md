# Q5811: revert with applied balance change via `try_decode_value` (mod.rs)

## Question
Can an unprivileged attacker who chains nested frames that touch balances, gas refunds and access lists in one transaction, controlling value, gas and access list, drive `try_decode_value` in `crates/evm/src/evm/mod.rs` so that the balance changes journaled during a reverted frame and the balance changes committed stop being the same set, breaking the invariant that a reverted frame commits nothing but gas and fees?

## Target
- File/function: `crates/evm/src/evm/mod.rs` -> `try_decode_value`
- Entrypoint: unprivileged party chains nested frames that touch balances, gas refunds and access lists in one transaction
- Attacker controls: value, gas and access list
- Exploit idea: revert with applied balance change - reach `try_decode_value` from that entrypoint and force the divergence where the balance changes journaled during a reverted frame and the balance changes committed stop being the same set; the adjacent symbols in the same file that carry the value are `AccountInfo`, `EvmChainConfig`, `deserialize_reader`, `encode_value`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a reverted frame commits nothing but gas and fees
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: revert after a system-contract call and assert balances are restored
