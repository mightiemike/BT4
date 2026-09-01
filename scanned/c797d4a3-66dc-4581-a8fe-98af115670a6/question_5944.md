# Q5944: difficulty/epoch adjustment via `verify_target_hash` (verifier.rs)

## Question
Can an unprivileged attacker who constructs a Bitcoin block whose coinbase witness commitment is structurally unusual, controlling the block's transaction set and coinbase, drive `verify_target_hash` in `crates/bitcoin-da/src/verifier.rs` so that the target the verifier derives and the target the network actually used stop being equal, breaking the invariant that difficulty rules match Bitcoin consensus?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `verify_target_hash`
- Entrypoint: unprivileged party constructs a Bitcoin block whose coinbase witness commitment is structurally unusual
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: difficulty/epoch adjustment - reach `verify_target_hash` from that entrypoint and force the divergence where the target the verifier derives and the target the network actually used stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: difficulty rules match Bitcoin consensus
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: cross an epoch boundary in regtest/signet and compare
