# Q5769: receipt root / log bloom construction via `contains` (handler.rs)

## Question
Can an unprivileged attacker who chains nested frames that touch balances, gas refunds and access lists in one transaction, controlling contract bytecode and calldata, drive `contains` in `crates/evm/src/evm/handler.rs` so that the receipt root the header commits and the root recomputed from the executed receipts stop being equal, breaking the invariant that the header commits exactly the executed receipts?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `contains`
- Entrypoint: unprivileged party chains nested frames that touch balances, gas refunds and access lists in one transaction
- Attacker controls: contract bytecode and calldata
- Exploit idea: receipt root / log bloom construction - reach `contains` from that entrypoint and force the divergence where the receipt root the header commits and the root recomputed from the executed receipts stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the header commits exactly the executed receipts
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: recompute receipts for an adversarial block and diff
