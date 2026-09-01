# Q5805: receipt root / log bloom construction via `validate_tx_against_state` (handler.rs)

## Question
Can an unprivileged attacker who sends a transaction that reverts after writing large state diffs, controlling value, gas and access list, drive `validate_tx_against_state` in `crates/evm/src/evm/handler.rs` so that the receipt root the header commits and the root recomputed from the executed receipts stop being equal, breaking the invariant that the header commits exactly the executed receipts?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `validate_tx_against_state`
- Entrypoint: unprivileged party sends a transaction that reverts after writing large state diffs
- Attacker controls: value, gas and access list
- Exploit idea: receipt root / log bloom construction - reach `validate_tx_against_state` from that entrypoint and force the divergence where the receipt root the header commits and the root recomputed from the executed receipts stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the header commits exactly the executed receipts
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: recompute receipts for an adversarial block and diff
