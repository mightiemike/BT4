# Q5785: revert with applied balance change via `diff_size_send_eth_eoa` (handler.rs)

## Question
Can an unprivileged attacker who sends a transaction whose calldata maximises the computed L1 diff size, controlling calldata entropy, drive `diff_size_send_eth_eoa` in `crates/evm/src/evm/handler.rs` so that the balance changes journaled during a reverted frame and the balance changes committed stop being the same set, breaking the invariant that a reverted frame commits nothing but gas and fees?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `diff_size_send_eth_eoa`
- Entrypoint: unprivileged party sends a transaction whose calldata maximises the computed L1 diff size
- Attacker controls: calldata entropy
- Exploit idea: revert with applied balance change - reach `diff_size_send_eth_eoa` from that entrypoint and force the divergence where the balance changes journaled during a reverted frame and the balance changes committed stop being the same set; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a reverted frame commits nothing but gas and fees
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: revert after a system-contract call and assert balances are restored
