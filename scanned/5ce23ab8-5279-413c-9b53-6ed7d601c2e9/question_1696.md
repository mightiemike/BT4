# Q1696: short header proof cache via `clear_queried_hashes` (native.rs)

## Question
Can an unprivileged attacker who forces a node to request a short header proof for an L1 hash of the attacker's choosing, controlling the L1 height the contract call references, drive `clear_queried_hashes` in `crates/short-header-proof-provider/src/native.rs` so that the short header proof served from cache and the proof for the requested hash stop being the same, breaking the invariant that cached proofs are keyed by the hash they prove?

## Target
- File/function: `crates/short-header-proof-provider/src/native.rs` -> `clear_queried_hashes`
- Entrypoint: unprivileged party forces a node to request a short header proof for an L1 hash of the attacker's choosing
- Attacker controls: the L1 height the contract call references
- Exploit idea: short header proof cache - reach `clear_queried_hashes` from that entrypoint and force the divergence where the short header proof served from cache and the proof for the requested hash stop being the same; the adjacent symbols in the same file that carry the value are `NativeShortHeaderProofProviderService`, `get_and_verify_short_header_proof_by_l1_hash`, `take_queried_hashes`, `take_last_queried_hash`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: cached proofs are keyed by the hash they prove
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: prime the cache and request a different hash
