# Q5720: timestamp/median-time rule via `coinbase_txid_merkle_proof_height` (header.rs)

## Question
Can an unprivileged attacker who mines a block whose header fields sit at a consensus boundary, controlling the block's transaction set and coinbase, drive `coinbase_txid_merkle_proof_height` in `crates/bitcoin-da/src/spec/header.rs` so that the timestamp the verifier accepts and the timestamp Bitcoin consensus allows stop being the same set, breaking the invariant that header validation is no weaker than Bitcoin's?

## Target
- File/function: `crates/bitcoin-da/src/spec/header.rs` -> `coinbase_txid_merkle_proof_height`
- Entrypoint: unprivileged party mines a block whose header fields sit at a consensus boundary
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: timestamp/median-time rule - reach `coinbase_txid_merkle_proof_height` from that entrypoint and force the divergence where the timestamp the verifier accepts and the timestamp Bitcoin consensus allows stop being the same set; the adjacent symbols in the same file that carry the value are `HeaderWrapper`, `BitcoinHeaderWrapper`, `prev_hash`, `verify_hash`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: header validation is no weaker than Bitcoin's
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed boundary timestamps and compare against bitcoind
