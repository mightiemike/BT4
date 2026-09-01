# Q1386: block hash versus header fields via `verify_header_chain_mainnet` (verifier.rs)

## Question
Can an unprivileged attacker who mines a block whose header fields sit at a consensus boundary, controlling the proof pair it induces the node to build, drive `verify_header_chain_mainnet` in `crates/bitcoin-da/src/verifier.rs` so that the block hash used as a key and the hash recomputed from the header stop being equal, breaking the invariant that block identity is derived, never taken on trust?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `verify_header_chain_mainnet`
- Entrypoint: unprivileged party mines a block whose header fields sit at a consensus boundary
- Attacker controls: the proof pair it induces the node to build
- Exploit idea: block hash versus header fields - reach `verify_header_chain_mainnet` from that entrypoint and force the divergence where the block hash used as a key and the hash recomputed from the header stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block identity is derived, never taken on trust
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: supply a header whose stored hash disagrees and assert rejection
