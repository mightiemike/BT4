# Q3156: monitoring/reveal restore via `commit_txid` (tx_signer.rs)

## Question
Can an unprivileged attacker who times Bitcoin transactions so the node restarts mid-broadcast, controlling block position and RBF replacement, drive `commit_txid` in `crates/bitcoin-da/src/tx_signer.rs` so that the reveal transaction the node restores after restart and the one it originally broadcast stop being the same transaction, breaking the invariant that restart never changes what was published?

## Target
- File/function: `crates/bitcoin-da/src/tx_signer.rs` -> `commit_txid`
- Entrypoint: unprivileged party times Bitcoin transactions so the node restarts mid-broadcast
- Attacker controls: block position and RBF replacement
- Exploit idea: monitoring/reveal restore - reach `commit_txid` from that entrypoint and force the divergence where the reveal transaction the node restores after restart and the one it originally broadcast stop being the same transaction; the adjacent symbols in the same file that carry the value are `SignedTxWithId`, `SignedTxPair`, `TxSigner`, `as_raw_txs`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: restart never changes what was published
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: restart mid-broadcast and diff the published set
