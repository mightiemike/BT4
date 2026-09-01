# Q5952: block hash versus header fields via `calculate_new_difficulty` (verifier.rs)

## Question
Can an unprivileged attacker who constructs a Bitcoin block whose coinbase witness commitment is structurally unusual, controlling header fields at the boundary, drive `calculate_new_difficulty` in `crates/bitcoin-da/src/verifier.rs` so that the block hash used as a key and the hash recomputed from the header stop being equal, breaking the invariant that block identity is derived, never taken on trust?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `calculate_new_difficulty`
- Entrypoint: unprivileged party constructs a Bitcoin block whose coinbase witness commitment is structurally unusual
- Attacker controls: header fields at the boundary
- Exploit idea: block hash versus header fields - reach `calculate_new_difficulty` from that entrypoint and force the divergence where the block hash used as a key and the hash recomputed from the header stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block identity is derived, never taken on trust
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: supply a header whose stored hash disagrees and assert rejection
