# Q3162: state transition chaining loop via `main` (light_client_proof_bitcoin.rs)

## Question
Can an unprivileged attacker who arranges the L1 data so the chaining loop is offered a mismatched initial root, controlling the initial and final roots the offered data claims, drive `main` in `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` so that the state root the chaining loop advances to and the root the batch proof for that index proved stop being the same root, breaking the invariant that chaining only advances on matching initial roots?

## Target
- File/function: `guests/risc0/light-client-proof/bitcoin/src/bin/light_client_proof_bitcoin.rs` -> `main`
- Entrypoint: unprivileged party arranges the L1 data so the chaining loop is offered a mismatched initial root
- Attacker controls: the initial and final roots the offered data claims
- Exploit idea: state transition chaining loop - reach `main` from that entrypoint and force the divergence where the state root the chaining loop advances to and the root the batch proof for that index proved stop being the same root; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: chaining only advances on matching initial roots
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: offer a proof with a mismatched initial root and assert no advance
