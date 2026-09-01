# Q5057: receipt root / log bloom construction via `execute_multiple_tx` (executor.rs)

## Question
Can an unprivileged attacker who sends a transaction that reverts after writing large state diffs, controlling calldata entropy, drive `execute_multiple_tx` in `crates/evm/src/evm/executor.rs` so that the receipt root the header commits and the root recomputed from the executed receipts stop being equal, breaking the invariant that the header commits exactly the executed receipts?

## Target
- File/function: `crates/evm/src/evm/executor.rs` -> `execute_multiple_tx`
- Entrypoint: unprivileged party sends a transaction that reverts after writing large state diffs
- Attacker controls: calldata entropy
- Exploit idea: receipt root / log bloom construction - reach `execute_multiple_tx` from that entrypoint and force the divergence where the receipt root the header commits and the root recomputed from the executed receipts stop being equal; the adjacent symbols in the same file that carry the value are `CitreaEvm`, `transact`, `commit`, `verify_system_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the header commits exactly the executed receipts
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: recompute receipts for an adversarial block and diff
