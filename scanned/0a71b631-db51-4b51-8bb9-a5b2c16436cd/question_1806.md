# Q1806: short header proof binding via `get_and_verify_short_header_proof_by_l1_hash` (zk.rs)

## Question
Can an unprivileged attacker who forces a node to request a short header proof for an L1 hash of the attacker's choosing, controlling the L1 height the contract call references, drive `get_and_verify_short_header_proof_by_l1_hash` in `crates/short-header-proof-provider/src/zk.rs` so that the L1 hash a short header proof claims and the header it actually proves stop being the same block, breaking the invariant that short header proofs are bound to their hash?

## Target
- File/function: `crates/short-header-proof-provider/src/zk.rs` -> `get_and_verify_short_header_proof_by_l1_hash`
- Entrypoint: unprivileged party forces a node to request a short header proof for an L1 hash of the attacker's choosing
- Attacker controls: the L1 height the contract call references
- Exploit idea: short header proof binding - reach `get_and_verify_short_header_proof_by_l1_hash` from that entrypoint and force the divergence where the L1 hash a short header proof claims and the header it actually proves stop being the same block; the adjacent symbols in the same file that carry the value are `ZkShortHeaderProofProviderService`, `clear_queried_hashes`, `take_queried_hashes`, `take_last_queried_hash`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: short header proofs are bound to their hash
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: swap the body under a requested hash and assert rejection
