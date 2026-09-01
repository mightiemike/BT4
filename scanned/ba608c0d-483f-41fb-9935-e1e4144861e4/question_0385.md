# Q0385: state transition chaining loop via `build_services` (services.rs)

## Question
Can an unprivileged attacker who inscribes commitments and proofs that induce a gap in the verified chain, controlling the initial and final roots the offered data claims, drive `build_services` in `crates/light-client-prover/src/services.rs` so that the state root the chaining loop advances to and the root the batch proof for that index proved stop being the same root, breaking the invariant that chaining only advances on matching initial roots?

## Target
- File/function: `crates/light-client-prover/src/services.rs` -> `build_services`
- Entrypoint: unprivileged party inscribes commitments and proofs that induce a gap in the verified chain
- Attacker controls: the initial and final roots the offered data claims
- Exploit idea: state transition chaining loop - reach `build_services` from that entrypoint and force the divergence where the state root the chaining loop advances to and the root the batch proof for that index proved stop being the same root; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: chaining only advances on matching initial roots
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: offer a proof with a mismatched initial root and assert no advance
