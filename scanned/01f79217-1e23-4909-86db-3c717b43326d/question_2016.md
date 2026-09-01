# Q2016: utxo selection reuse via `handle_reorg` (monitoring.rs)

## Question
Can an unprivileged attacker who broadcasts a conflicting spend of a UTXO the node intends to use, controlling fee-rate pressure on the mempool, drive `handle_reorg` in `crates/bitcoin-da/src/monitoring.rs` so that the UTXO the service believes it holds and the UTXO Bitcoin says is unspent stop being the same, breaking the invariant that the node never double-spends its own DA funding?

## Target
- File/function: `crates/bitcoin-da/src/monitoring.rs` -> `handle_reorg`
- Entrypoint: unprivileged party broadcasts a conflicting spend of a UTXO the node intends to use
- Attacker controls: fee-rate pressure on the mempool
- Exploit idea: utxo selection reuse - reach `handle_reorg` from that entrypoint and force the divergence where the UTXO the service believes it holds and the UTXO Bitcoin says is unspent stop being the same; the adjacent symbols in the same file that carry the value are `TxStatus`, `MonitoredTxKind`, `MonitoredTx`, `ChainState`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the node never double-spends its own DA funding
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: race two publishes on one UTXO and assert serialisation
