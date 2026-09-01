# Q0627: revert with applied balance change via `set_current_tx_hash` (handler.rs)

## Question
Can an unprivileged attacker who deploys a contract and calls it in the same L2 block, controlling revert timing inside the frame, drive `set_current_tx_hash` in `crates/evm/src/evm/handler.rs` so that the balance changes journaled during a reverted frame and the balance changes committed stop being the same set, breaking the invariant that a reverted frame commits nothing but gas and fees?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `set_current_tx_hash`
- Entrypoint: unprivileged party deploys a contract and calls it in the same L2 block
- Attacker controls: revert timing inside the frame
- Exploit idea: revert with applied balance change - reach `set_current_tx_hash` from that entrypoint and force the divergence where the balance changes journaled during a reverted frame and the balance changes committed stop being the same set; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a reverted frame commits nothing but gas and fees
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: revert after a system-contract call and assert balances are restored
