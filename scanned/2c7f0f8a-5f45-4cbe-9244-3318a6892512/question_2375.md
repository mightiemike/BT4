# Q2375: proof-of-a-proof method id check via `lib` (lib.rs)

## Question
Can an unprivileged attacker who inscribes commitments and proofs that induce a gap in the verified chain, controlling the initial and final roots the offered data claims, drive `lib` in `crates/light-client-prover/src/lib.rs` so that the method id used to verify a batch proof and the id authorised at that L2 height stop being the same, breaking the invariant that proofs are verified under the authorised circuit?

## Target
- File/function: `crates/light-client-prover/src/lib.rs` -> `lib`
- Entrypoint: unprivileged party inscribes commitments and proofs that induce a gap in the verified chain
- Attacker controls: the initial and final roots the offered data claims
- Exploit idea: proof-of-a-proof method id check - reach `lib` from that entrypoint and force the divergence where the method id used to verify a batch proof and the id authorised at that L2 height stop being the same; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: proofs are verified under the authorised circuit
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: verify a proof produced by a stale method id and assert rejection
