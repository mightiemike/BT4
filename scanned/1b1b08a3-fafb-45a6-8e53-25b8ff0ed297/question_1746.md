# Q1746: short header proof cache via `take_queried_hashes` (native.rs)

## Question
Can an unprivileged attacker who sends an L2 transaction that makes a contract query an L1 height with no stored proof, controlling the L1 height the contract call references, drive `take_queried_hashes` in `crates/short-header-proof-provider/src/native.rs` so that the short header proof served from cache and the proof for the requested hash stop being the same, breaking the invariant that cached proofs are keyed by the hash they prove?

## Target
- File/function: `crates/short-header-proof-provider/src/native.rs` -> `take_queried_hashes`
- Entrypoint: unprivileged party sends an L2 transaction that makes a contract query an L1 height with no stored proof
- Attacker controls: the L1 height the contract call references
- Exploit idea: short header proof cache - reach `take_queried_hashes` from that entrypoint and force the divergence where the short header proof served from cache and the proof for the requested hash stop being the same; the adjacent symbols in the same file that carry the value are `NativeShortHeaderProofProviderService`, `get_and_verify_short_header_proof_by_l1_hash`, `clear_queried_hashes`, `take_last_queried_hash`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: cached proofs are keyed by the hash they prove
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: prime the cache and request a different hash
