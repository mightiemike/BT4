# Q2275: sender check applied to the wrong field via `main` (light_client_proof_bitcoin.rs)

## Question
Can an unprivileged attacker who times inscriptions so two honest provers observe different blob ordering, controlling the exact byte encoding on the parse boundary, drive `main` in `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` so that the key compared against `batch_prover_da_public_key` and the key that authorised the blob stop being the same key, breaking the invariant that prover-authored blobs are authenticated before use?

## Target
- File/function: `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` -> `main`
- Entrypoint: unprivileged party times inscriptions so two honest provers observe different blob ordering
- Attacker controls: the exact byte encoding on the parse boundary
- Exploit idea: sender check applied to the wrong field - reach `main` from that entrypoint and force the divergence where the key compared against `batch_prover_da_public_key` and the key that authorised the blob stop being the same key; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: prover-authored blobs are authenticated before use
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe with a lookalike script and assert the sender check fails closed
