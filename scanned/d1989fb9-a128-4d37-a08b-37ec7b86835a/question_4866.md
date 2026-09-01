# Q4866: difficulty/epoch adjustment via `coinbase_txid_merkle_proof_height` (header.rs)

## Question
Can an unprivileged attacker who supplies a block whose inclusion and completeness proofs disagree, controlling the block's transaction set and coinbase, drive `coinbase_txid_merkle_proof_height` in `crates/bitcoin-da/src/spec/header.rs` so that the target the verifier derives and the target the network actually used stop being equal, breaking the invariant that difficulty rules match Bitcoin consensus?

## Target
- File/function: `crates/bitcoin-da/src/spec/header.rs` -> `coinbase_txid_merkle_proof_height`
- Entrypoint: unprivileged party supplies a block whose inclusion and completeness proofs disagree
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: difficulty/epoch adjustment - reach `coinbase_txid_merkle_proof_height` from that entrypoint and force the divergence where the target the verifier derives and the target the network actually used stop being equal; the adjacent symbols in the same file that carry the value are `HeaderWrapper`, `BitcoinHeaderWrapper`, `prev_hash`, `verify_hash`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: difficulty rules match Bitcoin consensus
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: cross an epoch boundary in regtest/signet and compare
