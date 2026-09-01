# Q0134: short header proof binding via `verify` (short_proof.rs)

## Question
Can an unprivileged attacker who sends an L2 transaction that makes a contract query an L1 height with no stored proof, controlling the L1 height the contract call references, drive `verify` in `crates/bitcoin-da/src/spec/short_proof.rs` so that the L1 hash a short header proof claims and the header it actually proves stop being the same block, breaking the invariant that short header proofs are bound to their hash?

## Target
- File/function: `crates/bitcoin-da/src/spec/short_proof.rs` -> `verify`
- Entrypoint: unprivileged party sends an L2 transaction that makes a contract query an L1 height with no stored proof
- Attacker controls: the L1 height the contract call references
- Exploit idea: short header proof binding - reach `verify` from that entrypoint and force the divergence where the L1 hash a short header proof claims and the header it actually proves stop being the same block; the adjacent symbols in the same file that carry the value are `BitcoinHeaderShortProof`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: short header proofs are bound to their hash
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: swap the body under a requested hash and assert rejection
