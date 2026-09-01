# Q0320: commitment range off-by-one via `run` (service.rs)

## Question
Can an unprivileged attacker who sends transactions sized to sit exactly at the commitment blob size threshold, controlling the L2 height at which its transactions land, drive `run` in `crates/sequencer/src/commitment/service.rs` so that the L2 height range a sequencer commitment covers and the range its merkle root was built from stop being the same range, breaking the invariant that a commitment's root commits exactly its declared range?

## Target
- File/function: `crates/sequencer/src/commitment/service.rs` -> `run`
- Entrypoint: unprivileged party sends transactions sized to sit exactly at the commitment blob size threshold
- Attacker controls: the L2 height at which its transactions land
- Exploit idea: commitment range off-by-one - reach `run` from that entrypoint and force the divergence where the L2 height range a sequencer commitment covers and the range its merkle root was built from stop being the same range; the adjacent symbols in the same file that carry the value are `CommitmentService`, `commit`, `store_commitments_from_da`, `get_commitment`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a commitment's root commits exactly its declared range
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: build a commitment at a range edge and re-derive its root
