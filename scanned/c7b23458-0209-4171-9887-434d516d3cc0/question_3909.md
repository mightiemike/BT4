# Q3909: commitment merkle leaf ordering via `get_mined_commitments_from` (service.rs)

## Question
Can an unprivileged attacker who sends transactions sized to sit exactly at the commitment blob size threshold, controlling the L2 height at which its transactions land, drive `get_mined_commitments_from` in `crates/sequencer/src/commitment/service.rs` so that the leaf order the commitment root was built from and the order a verifier reconstructs stop being the same, breaking the invariant that commitment roots are order-canonical?

## Target
- File/function: `crates/sequencer/src/commitment/service.rs` -> `get_mined_commitments_from`
- Entrypoint: unprivileged party sends transactions sized to sit exactly at the commitment blob size threshold
- Attacker controls: the L2 height at which its transactions land
- Exploit idea: commitment merkle leaf ordering - reach `get_mined_commitments_from` from that entrypoint and force the divergence where the leaf order the commitment root was built from and the order a verifier reconstructs stop being the same; the adjacent symbols in the same file that carry the value are `CommitmentService`, `run`, `commit`, `store_commitments_from_da`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitment roots are order-canonical
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: reconstruct the root from stored blocks and compare
