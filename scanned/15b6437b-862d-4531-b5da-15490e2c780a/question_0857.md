# Q0857: fee charge underflow at the balance edge via `get_tx_info` (handler.rs)

## Question
Can an unprivileged attacker who sends a transaction whose calldata maximises the computed L1 diff size, controlling revert timing inside the frame, drive `get_tx_info` in `crates/evm/src/evm/handler.rs` so that the balance debited for gas plus L1 fee and the balance the account actually holds stop being reconcilable, breaking the invariant that fee charging never underflows or silently skips?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `get_tx_info`
- Entrypoint: unprivileged party sends a transaction whose calldata maximises the computed L1 diff size
- Attacker controls: revert timing inside the frame
- Exploit idea: fee charge underflow at the balance edge - reach `get_tx_info` from that entrypoint and force the divergence where the balance debited for gas plus L1 fee and the balance the account actually holds stop being reconcilable; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fee charging never underflows or silently skips
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: execute at the exact balance boundary and assert exact accounting
