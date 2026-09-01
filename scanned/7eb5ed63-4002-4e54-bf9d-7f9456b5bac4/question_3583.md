# Q3583: state diff size non-determinism via `diff_size_send_eth_eoa` (handler.rs)

## Question
Can an unprivileged attacker who chains nested frames that touch balances, gas refunds and access lists in one transaction, controlling contract bytecode and calldata, drive `diff_size_send_eth_eoa` in `crates/evm/src/evm/handler.rs` so that the diff size computed natively and the diff size recomputed in the guest stop being equal, breaking the invariant that L1 fee inputs are deterministic across native and zk execution?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `diff_size_send_eth_eoa`
- Entrypoint: unprivileged party chains nested frames that touch balances, gas refunds and access lists in one transaction
- Attacker controls: contract bytecode and calldata
- Exploit idea: state diff size non-determinism - reach `diff_size_send_eth_eoa` from that entrypoint and force the divergence where the diff size computed natively and the diff size recomputed in the guest stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: L1 fee inputs are deterministic across native and zk execution
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: recompute the diff in a guest replay and compare
