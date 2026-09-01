# Q1195: l2 genesis root assumption via `main` (light_client_proof_bitcoin.rs)

## Question
Can an unprivileged attacker who arranges the L1 data so the chaining loop is offered a mismatched initial root, controlling the chunk/aggregate graph it plants, drive `main` in `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` so that the genesis root the circuit starts from with no previous proof and the network's real genesis root stop being equal, breaking the invariant that the bootstrap root is pinned?

## Target
- File/function: `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` -> `main`
- Entrypoint: unprivileged party arranges the L1 data so the chaining loop is offered a mismatched initial root
- Attacker controls: the chunk/aggregate graph it plants
- Exploit idea: l2 genesis root assumption - reach `main` from that entrypoint and force the divergence where the genesis root the circuit starts from with no previous proof and the network's real genesis root stop being equal; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the bootstrap root is pinned
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: start with no previous output and assert the pinned root
