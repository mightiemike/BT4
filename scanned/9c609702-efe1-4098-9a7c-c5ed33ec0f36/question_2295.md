# Q2295: journal hash preimage binding via `main` (light_client_proof_bitcoin.rs)

## Question
Can an unprivileged attacker who inscribes commitments and proofs that induce a gap in the verified chain, controlling the initial and final roots the offered data claims, drive `main` in `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` so that the fields hashed into the committed journal and the fields the verifier re-derives stop being the same tuple, breaking the invariant that journal commitment covers every field a consumer trusts?

## Target
- File/function: `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` -> `main`
- Entrypoint: unprivileged party inscribes commitments and proofs that induce a gap in the verified chain
- Attacker controls: the initial and final roots the offered data claims
- Exploit idea: journal hash preimage binding - reach `main` from that entrypoint and force the divergence where the fields hashed into the committed journal and the fields the verifier re-derives stop being the same tuple; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: journal commitment covers every field a consumer trusts
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: mutate an uncommitted field and assert the journal changes
