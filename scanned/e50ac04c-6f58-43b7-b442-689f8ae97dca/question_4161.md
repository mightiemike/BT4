# Q4161: monitoring/reveal restore via `get_available_utxos` (utxo_manager.rs)

## Question
Can an unprivileged attacker who broadcasts a conflicting spend of a UTXO the node intends to use, controlling the conflicting spend it broadcasts, drive `get_available_utxos` in `crates/bitcoin-da/src/utxo_manager.rs` so that the reveal transaction the node restores after restart and the one it originally broadcast stop being the same transaction, breaking the invariant that restart never changes what was published?

## Target
- File/function: `crates/bitcoin-da/src/utxo_manager.rs` -> `get_available_utxos`
- Entrypoint: unprivileged party broadcasts a conflicting spend of a UTXO the node intends to use
- Attacker controls: the conflicting spend it broadcasts
- Exploit idea: monitoring/reveal restore - reach `get_available_utxos` from that entrypoint and force the divergence where the reveal transaction the node restores after restart and the one it originally broadcast stop being the same transaction; the adjacent symbols in the same file that carry the value are `UtxoSelectionMode`, `UtxoContext`, `UtxoManager`, `prepare_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: restart never changes what was published
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: restart mid-broadcast and diff the published set
