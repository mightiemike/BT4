# Q2146: fee bumping changes blob identity via `prune_old_transactions` (monitoring.rs)

## Question
Can an unprivileged attacker who RBFs an inscription so two candidate reveals exist for the same logical blob, controlling fee-rate pressure on the mempool, drive `prune_old_transactions` in `crates/bitcoin-da/src/monitoring.rs` so that the blob identity before an RBF and the identity after stop being the same, breaking the invariant that fee bumping preserves blob semantics?

## Target
- File/function: `crates/bitcoin-da/src/monitoring.rs` -> `prune_old_transactions`
- Entrypoint: unprivileged party RBFs an inscription so two candidate reveals exist for the same logical blob
- Attacker controls: fee-rate pressure on the mempool
- Exploit idea: fee bumping changes blob identity - reach `prune_old_transactions` from that entrypoint and force the divergence where the blob identity before an RBF and the identity after stop being the same; the adjacent symbols in the same file that carry the value are `TxStatus`, `MonitoredTxKind`, `MonitoredTx`, `ChainState`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fee bumping preserves blob semantics
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: RBF a reveal and assert the parsed body is unchanged
