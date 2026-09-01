# Q3756: monitoring/reveal restore via `restore_from_utxos` (monitoring.rs)

## Question
Can an unprivileged attacker who RBFs an inscription so two candidate reveals exist for the same logical blob, controlling block position and RBF replacement, drive `restore_from_utxos` in `crates/bitcoin-da/src/monitoring.rs` so that the reveal transaction the node restores after restart and the one it originally broadcast stop being the same transaction, breaking the invariant that restart never changes what was published?

## Target
- File/function: `crates/bitcoin-da/src/monitoring.rs` -> `restore_from_utxos`
- Entrypoint: unprivileged party RBFs an inscription so two candidate reveals exist for the same logical blob
- Attacker controls: block position and RBF replacement
- Exploit idea: monitoring/reveal restore - reach `restore_from_utxos` from that entrypoint and force the divergence where the reveal transaction the node restores after restart and the one it originally broadcast stop being the same transaction; the adjacent symbols in the same file that carry the value are `TxStatus`, `MonitoredTxKind`, `MonitoredTx`, `ChainState`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: restart never changes what was published
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: restart mid-broadcast and diff the published set
