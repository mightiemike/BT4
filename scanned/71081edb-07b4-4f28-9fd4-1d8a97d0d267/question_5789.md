# Q5789: gas refund/limit interaction with L1 fee via `diff_size_send_eth_eoa` (handler.rs)

## Question
Can an unprivileged attacker who chains nested frames that touch balances, gas refunds and access lists in one transaction, controlling revert timing inside the frame, drive `diff_size_send_eth_eoa` in `crates/evm/src/evm/handler.rs` so that the effective gas price used for refunds and the price used for the L1 fee charge stop being consistent, breaking the invariant that refunds never return more than was paid?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `diff_size_send_eth_eoa`
- Entrypoint: unprivileged party chains nested frames that touch balances, gas refunds and access lists in one transaction
- Attacker controls: revert timing inside the frame
- Exploit idea: gas refund/limit interaction with L1 fee - reach `diff_size_send_eth_eoa` from that entrypoint and force the divergence where the effective gas price used for refunds and the price used for the L1 fee charge stop being consistent; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: refunds never return more than was paid
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: maximise refunds under an L1-fee-heavy transaction and assert non-negative net
