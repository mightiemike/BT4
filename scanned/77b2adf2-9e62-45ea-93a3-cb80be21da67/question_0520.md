# Q0520: pending commitment overwrite via `get_mined_commitments_from` (service.rs)

## Question
Can an unprivileged attacker who sends transactions sized to sit exactly at the commitment blob size threshold, controlling transaction sizes at the blob threshold, drive `get_mined_commitments_from` in `crates/sequencer/src/commitment/service.rs` so that the commitment stored for an index and the commitment actually published to Bitcoin stop being the same object, breaking the invariant that stored commitments match published ones?

## Target
- File/function: `crates/sequencer/src/commitment/service.rs` -> `get_mined_commitments_from`
- Entrypoint: unprivileged party sends transactions sized to sit exactly at the commitment blob size threshold
- Attacker controls: transaction sizes at the blob threshold
- Exploit idea: pending commitment overwrite - reach `get_mined_commitments_from` from that entrypoint and force the divergence where the commitment stored for an index and the commitment actually published to Bitcoin stop being the same object; the adjacent symbols in the same file that carry the value are `CommitmentService`, `run`, `commit`, `store_commitments_from_da`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: stored commitments match published ones
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: publish then overwrite and assert the stored value tracks Bitcoin
