# Q2995: utxo selection reuse via `get_block_by_hash` (service.rs)

## Question
Can an unprivileged attacker who times Bitcoin transactions so the node restarts mid-broadcast, controlling fee-rate pressure on the mempool, drive `get_block_by_hash` in `crates/bitcoin-da/src/service.rs` so that the UTXO the service believes it holds and the UTXO Bitcoin says is unspent stop being the same, breaking the invariant that the node never double-spends its own DA funding?

## Target
- File/function: `crates/bitcoin-da/src/service.rs` -> `get_block_by_hash`
- Entrypoint: unprivileged party times Bitcoin transactions so the node restarts mid-broadcast
- Attacker controls: fee-rate pressure on the mempool
- Exploit idea: utxo selection reuse - reach `get_block_by_hash` from that entrypoint and force the divergence where the UTXO the service believes it holds and the UTXO Bitcoin says is unspent stop being the same; the adjacent symbols in the same file that carry the value are `BitcoinServiceConfig`, `BitcoinService`, `TxidWrapper`, `network_to_bitcoin_network`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the node never double-spends its own DA funding
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: race two publishes on one UTXO and assert serialisation
