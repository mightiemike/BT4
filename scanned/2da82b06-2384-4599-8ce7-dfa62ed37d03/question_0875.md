# Q0875: stale previous-proof reuse via `main` (light_client_proof_bitcoin.rs)

## Question
Can an unprivileged attacker who inscribes commitments and proofs that induce a gap in the verified chain, controlling which commitment indices are covered, drive `main` in `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` so that the previous LCP output a prover chains from and the output for the immediately preceding L1 block stop being the same, breaking the invariant that each LCP chains to its exact predecessor?

## Target
- File/function: `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` -> `main`
- Entrypoint: unprivileged party inscribes commitments and proofs that induce a gap in the verified chain
- Attacker controls: which commitment indices are covered
- Exploit idea: stale previous-proof reuse - reach `main` from that entrypoint and force the divergence where the previous LCP output a prover chains from and the output for the immediately preceding L1 block stop being the same; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each LCP chains to its exact predecessor
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: chain from an older output and assert rejection
