# Q1836: fee bumping changes blob identity via `restore_from_utxos` (monitoring.rs)

## Question
Can an unprivileged attacker who broadcasts a conflicting spend of a UTXO the node intends to use, controlling the conflicting spend it broadcasts, drive `restore_from_utxos` in `crates/bitcoin-da/src/monitoring.rs` so that the blob identity before an RBF and the identity after stop being the same, breaking the invariant that fee bumping preserves blob semantics?

## Target
- File/function: `crates/bitcoin-da/src/monitoring.rs` -> `restore_from_utxos`
- Entrypoint: unprivileged party broadcasts a conflicting spend of a UTXO the node intends to use
- Attacker controls: the conflicting spend it broadcasts
- Exploit idea: fee bumping changes blob identity - reach `restore_from_utxos` from that entrypoint and force the divergence where the blob identity before an RBF and the identity after stop being the same; the adjacent symbols in the same file that carry the value are `TxStatus`, `MonitoredTxKind`, `MonitoredTx`, `ChainState`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fee bumping preserves blob semantics
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: RBF a reveal and assert the parsed body is unchanged
