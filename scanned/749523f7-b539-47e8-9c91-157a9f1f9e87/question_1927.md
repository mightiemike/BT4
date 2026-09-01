# Q1927: receipt root / log bloom construction via `decrease_caller_balance` (handler.rs)

## Question
Can an unprivileged attacker who sends a transaction whose calldata maximises the computed L1 diff size, controlling calldata entropy, drive `decrease_caller_balance` in `crates/evm/src/evm/handler.rs` so that the receipt root the header commits and the root recomputed from the executed receipts stop being equal, breaking the invariant that the header commits exactly the executed receipts?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `decrease_caller_balance`
- Entrypoint: unprivileged party sends a transaction whose calldata maximises the computed L1 diff size
- Attacker controls: calldata entropy
- Exploit idea: receipt root / log bloom construction - reach `decrease_caller_balance` from that entrypoint and force the divergence where the receipt root the header commits and the root recomputed from the executed receipts stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the header commits exactly the executed receipts
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: recompute receipts for an adversarial block and diff
