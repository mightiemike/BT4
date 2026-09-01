# Q5112: state diff size non-determinism via `set_current_tx_hash` (handler.rs)

## Question
Can an unprivileged attacker who sends a transaction whose calldata maximises the computed L1 diff size, controlling revert timing inside the frame, drive `set_current_tx_hash` in `crates/evm/src/evm/handler.rs` so that the diff size computed natively and the diff size recomputed in the guest stop being equal, breaking the invariant that L1 fee inputs are deterministic across native and zk execution?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `set_current_tx_hash`
- Entrypoint: unprivileged party sends a transaction whose calldata maximises the computed L1 diff size
- Attacker controls: revert timing inside the frame
- Exploit idea: state diff size non-determinism - reach `set_current_tx_hash` from that entrypoint and force the divergence where the diff size computed natively and the diff size recomputed in the guest stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: L1 fee inputs are deterministic across native and zk execution
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: recompute the diff in a guest replay and compare
