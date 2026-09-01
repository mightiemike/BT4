# Q3296: fee bumping changes blob identity via `oldest_mode_filter` (utxo_manager.rs)

## Question
Can an unprivileged attacker who RBFs an inscription so two candidate reveals exist for the same logical blob, controlling fee-rate pressure on the mempool, drive `oldest_mode_filter` in `crates/bitcoin-da/src/utxo_manager.rs` so that the blob identity before an RBF and the identity after stop being the same, breaking the invariant that fee bumping preserves blob semantics?

## Target
- File/function: `crates/bitcoin-da/src/utxo_manager.rs` -> `oldest_mode_filter`
- Entrypoint: unprivileged party RBFs an inscription so two candidate reveals exist for the same logical blob
- Attacker controls: fee-rate pressure on the mempool
- Exploit idea: fee bumping changes blob identity - reach `oldest_mode_filter` from that entrypoint and force the divergence where the blob identity before an RBF and the identity after stop being the same; the adjacent symbols in the same file that carry the value are `UtxoSelectionMode`, `UtxoContext`, `UtxoManager`, `prepare_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fee bumping preserves blob semantics
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: RBF a reveal and assert the parsed body is unchanged
