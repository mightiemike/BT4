# Q4756: difficulty/epoch adjustment via `prev_hash` (header.rs)

## Question
Can an unprivileged attacker who supplies a block whose inclusion and completeness proofs disagree, controlling the number of prefix-matching reveals, drive `prev_hash` in `crates/bitcoin-da/src/spec/header.rs` so that the target the verifier derives and the target the network actually used stop being equal, breaking the invariant that difficulty rules match Bitcoin consensus?

## Target
- File/function: `crates/bitcoin-da/src/spec/header.rs` -> `prev_hash`
- Entrypoint: unprivileged party supplies a block whose inclusion and completeness proofs disagree
- Attacker controls: the number of prefix-matching reveals
- Exploit idea: difficulty/epoch adjustment - reach `prev_hash` from that entrypoint and force the divergence where the target the verifier derives and the target the network actually used stop being equal; the adjacent symbols in the same file that carry the value are `HeaderWrapper`, `BitcoinHeaderWrapper`, `verify_hash`, `txs_commitment`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: difficulty rules match Bitcoin consensus
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: cross an epoch boundary in regtest/signet and compare
