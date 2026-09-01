# Q2862: monitoring/reveal restore via `get_last_finalized_block_header` (service.rs)

## Question
Can an unprivileged attacker who RBFs an inscription so two candidate reveals exist for the same logical blob, controlling the conflicting spend it broadcasts, drive `get_last_finalized_block_header` in `crates/bitcoin-da/src/service.rs` so that the reveal transaction the node restores after restart and the one it originally broadcast stop being the same transaction, breaking the invariant that restart never changes what was published?

## Target
- File/function: `crates/bitcoin-da/src/service.rs` -> `get_last_finalized_block_header`
- Entrypoint: unprivileged party RBFs an inscription so two candidate reveals exist for the same logical blob
- Attacker controls: the conflicting spend it broadcasts
- Exploit idea: monitoring/reveal restore - reach `get_last_finalized_block_header` from that entrypoint and force the divergence where the reveal transaction the node restores after restart and the one it originally broadcast stop being the same transaction; the adjacent symbols in the same file that carry the value are `BitcoinServiceConfig`, `BitcoinService`, `TxidWrapper`, `network_to_bitcoin_network`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: restart never changes what was published
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: restart mid-broadcast and diff the published set
