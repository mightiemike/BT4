# Q2869: utxo selection reuse via `get_last_finalized_block_header` (service.rs)

## Question
Can an unprivileged attacker who broadcasts a conflicting spend of a UTXO the node intends to use, controlling the conflicting spend it broadcasts, drive `get_last_finalized_block_header` in `crates/bitcoin-da/src/service.rs` so that the UTXO the service believes it holds and the UTXO Bitcoin says is unspent stop being the same, breaking the invariant that the node never double-spends its own DA funding?

## Target
- File/function: `crates/bitcoin-da/src/service.rs` -> `get_last_finalized_block_header`
- Entrypoint: unprivileged party broadcasts a conflicting spend of a UTXO the node intends to use
- Attacker controls: the conflicting spend it broadcasts
- Exploit idea: utxo selection reuse - reach `get_last_finalized_block_header` from that entrypoint and force the divergence where the UTXO the service believes it holds and the UTXO Bitcoin says is unspent stop being the same; the adjacent symbols in the same file that carry the value are `BitcoinServiceConfig`, `BitcoinService`, `TxidWrapper`, `network_to_bitcoin_network`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the node never double-spends its own DA funding
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: race two publishes on one UTXO and assert serialisation
