# Q4654: commitment merkle leaf ordering via `store_commitments_from_da` (service.rs)

## Question
Can an unprivileged attacker who broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to, controlling the L2 height at which its transactions land, drive `store_commitments_from_da` in `crates/sequencer/src/commitment/service.rs` so that the leaf order the commitment root was built from and the order a verifier reconstructs stop being the same, breaking the invariant that commitment roots are order-canonical?

## Target
- File/function: `crates/sequencer/src/commitment/service.rs` -> `store_commitments_from_da`
- Entrypoint: unprivileged party broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to
- Attacker controls: the L2 height at which its transactions land
- Exploit idea: commitment merkle leaf ordering - reach `store_commitments_from_da` from that entrypoint and force the divergence where the leaf order the commitment root was built from and the order a verifier reconstructs stop being the same; the adjacent symbols in the same file that carry the value are `CommitmentService`, `run`, `commit`, `get_commitment`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitment roots are order-canonical
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: reconstruct the root from stored blocks and compare
