# Q3449: circuit input wiring via `main` (light_client_proof_bitcoin.rs)

## Question
Can an unprivileged attacker who inscribes a complete proof body that decompresses differently than it was chunked, controlling the initial and final roots the offered data claims, drive `main` in `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` so that the inputs the service hands the circuit and the inputs derived from verified DA data stop being the same, breaking the invariant that circuit inputs come only from verified DA data?

## Target
- File/function: `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` -> `main`
- Entrypoint: unprivileged party inscribes a complete proof body that decompresses differently than it was chunked
- Attacker controls: the initial and final roots the offered data claims
- Exploit idea: circuit input wiring - reach `main` from that entrypoint and force the divergence where the inputs the service hands the circuit and the inputs derived from verified DA data stop being the same; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: circuit inputs come only from verified DA data
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: inject an unverified field and assert the circuit refuses
