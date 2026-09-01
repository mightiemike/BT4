# Q3359: difficulty/epoch adjustment via `from_bytes` (mod.rs)

## Question
Can an unprivileged attacker who supplies a block whose inclusion and completeness proofs disagree, controlling the block's transaction set and coinbase, drive `from_bytes` in `crates/bitcoin-da/src/helpers/mod.rs` so that the target the verifier derives and the target the network actually used stop being equal, breaking the invariant that difficulty rules match Bitcoin consensus?

## Target
- File/function: `crates/bitcoin-da/src/helpers/mod.rs` -> `from_bytes`
- Entrypoint: unprivileged party supplies a block whose inclusion and completeness proofs disagree
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: difficulty/epoch adjustment - reach `from_bytes` from that entrypoint and force the divergence where the target the verifier derives and the target the network actually used stop being equal; the adjacent symbols in the same file that carry the value are `TransactionKind`, `to_bytes`, `calculate_double_sha256`, `calculate_txid`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: difficulty rules match Bitcoin consensus
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: cross an epoch boundary in regtest/signet and compare
