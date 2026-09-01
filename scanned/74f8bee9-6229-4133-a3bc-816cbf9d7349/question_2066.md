# Q2066: monitoring/reveal restore via `check_transactions` (monitoring.rs)

## Question
Can an unprivileged attacker who broadcasts a conflicting spend of a UTXO the node intends to use, controlling fee-rate pressure on the mempool, drive `check_transactions` in `crates/bitcoin-da/src/monitoring.rs` so that the reveal transaction the node restores after restart and the one it originally broadcast stop being the same transaction, breaking the invariant that restart never changes what was published?

## Target
- File/function: `crates/bitcoin-da/src/monitoring.rs` -> `check_transactions`
- Entrypoint: unprivileged party broadcasts a conflicting spend of a UTXO the node intends to use
- Attacker controls: fee-rate pressure on the mempool
- Exploit idea: monitoring/reveal restore - reach `check_transactions` from that entrypoint and force the divergence where the reveal transaction the node restores after restart and the one it originally broadcast stop being the same transaction; the adjacent symbols in the same file that carry the value are `TxStatus`, `MonitoredTxKind`, `MonitoredTx`, `ChainState`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: restart never changes what was published
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: restart mid-broadcast and diff the published set
