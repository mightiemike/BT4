# Q2365: commitment stored once semantics via `main` (light_client_proof_bitcoin.rs)

## Question
Can an unprivileged attacker who inscribes a complete proof body that decompresses differently than it was chunked, controlling the initial and final roots the offered data claims, drive `main` in `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` so that the commitment stored for an index and the first commitment seen for that index stop being the same object, breaking the invariant that the first valid commitment per index wins, deterministically?

## Target
- File/function: `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` -> `main`
- Entrypoint: unprivileged party inscribes a complete proof body that decompresses differently than it was chunked
- Attacker controls: the initial and final roots the offered data claims
- Exploit idea: commitment stored once semantics - reach `main` from that entrypoint and force the divergence where the commitment stored for an index and the first commitment seen for that index stop being the same object; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the first valid commitment per index wins, deterministically
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe two commitments for one index and assert stable selection
