# Q5928: block hash versus header fields via `target_to_work` (verifier.rs)

## Question
Can an unprivileged attacker who inscribes thousands of prefix-matching reveals in one Bitcoin block, controlling the block's transaction set and coinbase, drive `target_to_work` in `crates/bitcoin-da/src/verifier.rs` so that the block hash used as a key and the hash recomputed from the header stop being equal, breaking the invariant that block identity is derived, never taken on trust?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `target_to_work`
- Entrypoint: unprivileged party inscribes thousands of prefix-matching reveals in one Bitcoin block
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: block hash versus header fields - reach `target_to_work` from that entrypoint and force the divergence where the block hash used as a key and the hash recomputed from the header stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block identity is derived, never taken on trust
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: supply a header whose stored hash disagrees and assert rejection
