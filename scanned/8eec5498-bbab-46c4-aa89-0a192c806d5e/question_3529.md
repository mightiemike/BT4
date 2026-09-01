# Q3529: fee charge underflow at the balance edge via `contains` (handler.rs)

## Question
Can an unprivileged attacker who deploys a contract and calls it in the same L2 block, controlling value, gas and access list, drive `contains` in `crates/evm/src/evm/handler.rs` so that the balance debited for gas plus L1 fee and the balance the account actually holds stop being reconcilable, breaking the invariant that fee charging never underflows or silently skips?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `contains`
- Entrypoint: unprivileged party deploys a contract and calls it in the same L2 block
- Attacker controls: value, gas and access list
- Exploit idea: fee charge underflow at the balance edge - reach `contains` from that entrypoint and force the divergence where the balance debited for gas plus L1 fee and the balance the account actually holds stop being reconcilable; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fee charging never underflows or silently skips
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: execute at the exact balance boundary and assert exact accounting
