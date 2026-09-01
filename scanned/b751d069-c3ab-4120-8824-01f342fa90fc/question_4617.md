# Q4617: receipt root / log bloom construction via `execute_call` (call.rs)

## Question
Can an unprivileged attacker who deploys a contract and calls it in the same L2 block, controlling calldata entropy, drive `execute_call` in `crates/evm/src/call.rs` so that the receipt root the header commits and the root recomputed from the executed receipts stop being equal, breaking the invariant that the header commits exactly the executed receipts?

## Target
- File/function: `crates/evm/src/call.rs` -> `execute_call`
- Entrypoint: unprivileged party deploys a contract and calls it in the same L2 block
- Attacker controls: calldata entropy
- Exploit idea: receipt root / log bloom construction - reach `execute_call` from that entrypoint and force the divergence where the receipt root the header commits and the root recomputed from the executed receipts stop being equal; the adjacent symbols in the same file that carry the value are `CallMessage`, `get_cfg_env`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the header commits exactly the executed receipts
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: recompute receipts for an adversarial block and diff
