# Q0415: block hash accessor growth via `main` (light_client_proof_bitcoin.rs)

## Question
Can an unprivileged attacker who inscribes commitments and proofs that induce a gap in the verified chain, controlling the initial and final roots the offered data claims, drive `main` in `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` so that the L1 block hash set the circuit knows and the chain of hashes actually processed stop being the same chain, breaking the invariant that known hashes form the processed chain?

## Target
- File/function: `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` -> `main`
- Entrypoint: unprivileged party inscribes commitments and proofs that induce a gap in the verified chain
- Attacker controls: the initial and final roots the offered data claims
- Exploit idea: block hash accessor growth - reach `main` from that entrypoint and force the divergence where the L1 block hash set the circuit knows and the chain of hashes actually processed stop being the same chain; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: known hashes form the processed chain
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: reference an unknown L1 hash and assert rejection
