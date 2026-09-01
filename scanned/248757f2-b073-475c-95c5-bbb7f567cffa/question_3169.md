# Q3169: proof-of-a-proof method id check via `main` (light_client_proof_bitcoin.rs)

## Question
Can an unprivileged attacker who inscribes commitments and proofs that induce a gap in the verified chain, controlling which commitment indices are covered, drive `main` in `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` so that the method id used to verify a batch proof and the id authorised at that L2 height stop being the same, breaking the invariant that proofs are verified under the authorised circuit?

## Target
- File/function: `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` -> `main`
- Entrypoint: unprivileged party inscribes commitments and proofs that induce a gap in the verified chain
- Attacker controls: which commitment indices are covered
- Exploit idea: proof-of-a-proof method id check - reach `main` from that entrypoint and force the divergence where the method id used to verify a batch proof and the id authorised at that L2 height stop being the same; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: proofs are verified under the authorised circuit
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: verify a proof produced by a stale method id and assert rejection
