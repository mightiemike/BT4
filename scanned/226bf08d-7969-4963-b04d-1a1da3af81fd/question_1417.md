# Q1417: state diff size non-determinism via `inspect_replay` (handler.rs)

## Question
Can an unprivileged attacker who deploys a contract and calls it in the same L2 block, controlling calldata entropy, drive `inspect_replay` in `crates/evm/src/evm/handler.rs` so that the diff size computed natively and the diff size recomputed in the guest stop being equal, breaking the invariant that L1 fee inputs are deterministic across native and zk execution?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `inspect_replay`
- Entrypoint: unprivileged party deploys a contract and calls it in the same L2 block
- Attacker controls: calldata entropy
- Exploit idea: state diff size non-determinism - reach `inspect_replay` from that entrypoint and force the divergence where the diff size computed natively and the diff size recomputed in the guest stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: L1 fee inputs are deterministic across native and zk execution
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: recompute the diff in a guest replay and compare
