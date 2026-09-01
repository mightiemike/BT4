# Q5392: inclusion proof over a different tree via `verify_constants` (network_constants.rs)

## Question
Can an unprivileged attacker who supplies a block whose inclusion and completeness proofs disagree, controlling the proof pair it induces the node to build, drive `verify_constants` in `crates/bitcoin-da/src/network_constants.rs` so that the merkle root the inclusion proof reconstructs and the root in the block header stop being equal, breaking the invariant that inclusion proofs verify against the header?

## Target
- File/function: `crates/bitcoin-da/src/network_constants.rs` -> `verify_constants`
- Entrypoint: unprivileged party supplies a block whose inclusion and completeness proofs disagree
- Attacker controls: the proof pair it induces the node to build
- Exploit idea: inclusion proof over a different tree - reach `verify_constants` from that entrypoint and force the divergence where the merkle root the inclusion proof reconstructs and the root in the block header stop being equal; the adjacent symbols in the same file that carry the value are `NetworkConstants`, `get_network_constants`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: inclusion proofs verify against the header
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: supply a proof for a sibling block and assert rejection
