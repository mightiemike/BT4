# Q1616: merkle path length ambiguity via `target_to_work` (verifier.rs)

## Question
Can an unprivileged attacker who mines a block whose header fields sit at a consensus boundary, controlling the number of prefix-matching reveals, drive `target_to_work` in `crates/bitcoin-da/src/verifier.rs` so that the leaf position a proof path implies and the position the tree assigns stop being the same index, breaking the invariant that a merkle path determines exactly one leaf index?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `target_to_work`
- Entrypoint: unprivileged party mines a block whose header fields sit at a consensus boundary
- Attacker controls: the number of prefix-matching reveals
- Exploit idea: merkle path length ambiguity - reach `target_to_work` from that entrypoint and force the divergence where the leaf position a proof path implies and the position the tree assigns stop being the same index; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a merkle path determines exactly one leaf index
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: supply paths of unusual depth and assert index binding
