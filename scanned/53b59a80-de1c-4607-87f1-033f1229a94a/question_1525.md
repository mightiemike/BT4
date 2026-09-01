# Q1525: l2 genesis root assumption via `lib` (lib.rs)

## Question
Can an unprivileged attacker who inscribes commitments and proofs that induce a gap in the verified chain, controlling the initial and final roots the offered data claims, drive `lib` in `crates/light-client-prover/src/lib.rs` so that the genesis root the circuit starts from with no previous proof and the network's real genesis root stop being equal, breaking the invariant that the bootstrap root is pinned?

## Target
- File/function: `crates/light-client-prover/src/lib.rs` -> `lib`
- Entrypoint: unprivileged party inscribes commitments and proofs that induce a gap in the verified chain
- Attacker controls: the initial and final roots the offered data claims
- Exploit idea: l2 genesis root assumption - reach `lib` from that entrypoint and force the divergence where the genesis root the circuit starts from with no previous proof and the network's real genesis root stop being equal; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the bootstrap root is pinned
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: start with no previous output and assert the pinned root
