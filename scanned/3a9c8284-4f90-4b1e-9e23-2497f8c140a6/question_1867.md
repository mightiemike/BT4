# Q1867: state diff size non-determinism via `calc_diff_size` (handler.rs)

## Question
Can an unprivileged attacker who deploys a contract and calls it in the same L2 block, controlling contract bytecode and calldata, drive `calc_diff_size` in `crates/evm/src/evm/handler.rs` so that the diff size computed natively and the diff size recomputed in the guest stop being equal, breaking the invariant that L1 fee inputs are deterministic across native and zk execution?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `calc_diff_size`
- Entrypoint: unprivileged party deploys a contract and calls it in the same L2 block
- Attacker controls: contract bytecode and calldata
- Exploit idea: state diff size non-determinism - reach `calc_diff_size` from that entrypoint and force the divergence where the diff size computed natively and the diff size recomputed in the guest stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: L1 fee inputs are deterministic across native and zk execution
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: recompute the diff in a guest replay and compare
