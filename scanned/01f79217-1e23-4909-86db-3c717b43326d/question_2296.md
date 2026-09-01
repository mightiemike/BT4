# Q2296: fee bumping changes blob identity via `get_last_tx` (monitoring.rs)

## Question
Can an unprivileged attacker who times Bitcoin transactions so the node restarts mid-broadcast, controlling the conflicting spend it broadcasts, drive `get_last_tx` in `crates/bitcoin-da/src/monitoring.rs` so that the blob identity before an RBF and the identity after stop being the same, breaking the invariant that fee bumping preserves blob semantics?

## Target
- File/function: `crates/bitcoin-da/src/monitoring.rs` -> `get_last_tx`
- Entrypoint: unprivileged party times Bitcoin transactions so the node restarts mid-broadcast
- Attacker controls: the conflicting spend it broadcasts
- Exploit idea: fee bumping changes blob identity - reach `get_last_tx` from that entrypoint and force the divergence where the blob identity before an RBF and the identity after stop being the same; the adjacent symbols in the same file that carry the value are `TxStatus`, `MonitoredTxKind`, `MonitoredTx`, `ChainState`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fee bumping preserves blob semantics
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: RBF a reveal and assert the parsed body is unchanged
