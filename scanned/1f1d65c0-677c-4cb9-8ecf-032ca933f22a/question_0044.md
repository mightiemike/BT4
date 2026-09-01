# Q0044: merkle helper leaf handling via `compute_tx_hashes` (merkle.rs)

## Question
Can an unprivileged attacker who constructs input that makes a merkle helper receive an unusual leaf count, controlling the leaf set handed to the merkle helper, drive `compute_tx_hashes` in `crates/primitives/src/merkle.rs` so that the root computed for a leaf set and the root computed for a manipulated-but-equivalent set stop being distinct, breaking the invariant that merkle roots are second-preimage resistant for the used shapes?

## Target
- File/function: `crates/primitives/src/merkle.rs` -> `compute_tx_hashes`
- Entrypoint: unprivileged party constructs input that makes a merkle helper receive an unusual leaf count
- Attacker controls: the leaf set handed to the merkle helper
- Exploit idea: merkle helper leaf handling - reach `compute_tx_hashes` from that entrypoint and force the divergence where the root computed for a leaf set and the root computed for a manipulated-but-equivalent set stop being distinct; the adjacent symbols in the same file that carry the value are `Sha256WithSeparator`, `compute_tx_merkle_root`, `verify_tx_merkle_root`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: merkle roots are second-preimage resistant for the used shapes
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: test duplicate/odd leaf counts for root collisions
