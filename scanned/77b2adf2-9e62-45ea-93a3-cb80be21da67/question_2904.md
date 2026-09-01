# Q2904: monitoring/reveal restore via `extract_relevant_zk_proofs` (service.rs)

## Question
Can an unprivileged attacker who times Bitcoin transactions so the node restarts mid-broadcast, controlling the conflicting spend it broadcasts, drive `extract_relevant_zk_proofs` in `crates/bitcoin-da/src/service.rs` so that the reveal transaction the node restores after restart and the one it originally broadcast stop being the same transaction, breaking the invariant that restart never changes what was published?

## Target
- File/function: `crates/bitcoin-da/src/service.rs` -> `extract_relevant_zk_proofs`
- Entrypoint: unprivileged party times Bitcoin transactions so the node restarts mid-broadcast
- Attacker controls: the conflicting spend it broadcasts
- Exploit idea: monitoring/reveal restore - reach `extract_relevant_zk_proofs` from that entrypoint and force the divergence where the reveal transaction the node restores after restart and the one it originally broadcast stop being the same transaction; the adjacent symbols in the same file that carry the value are `BitcoinServiceConfig`, `BitcoinService`, `TxidWrapper`, `network_to_bitcoin_network`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: restart never changes what was published
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: restart mid-broadcast and diff the published set
