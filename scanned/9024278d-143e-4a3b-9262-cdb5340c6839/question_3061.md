# Q3061: pending commitment overwrite via `get_pending_mempool_commitments` (service.rs)

## Question
Can an unprivileged attacker who broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to, controlling transaction sizes at the blob threshold, drive `get_pending_mempool_commitments` in `crates/sequencer/src/commitment/service.rs` so that the commitment stored for an index and the commitment actually published to Bitcoin stop being the same object, breaking the invariant that stored commitments match published ones?

## Target
- File/function: `crates/sequencer/src/commitment/service.rs` -> `get_pending_mempool_commitments`
- Entrypoint: unprivileged party broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to
- Attacker controls: transaction sizes at the blob threshold
- Exploit idea: pending commitment overwrite - reach `get_pending_mempool_commitments` from that entrypoint and force the divergence where the commitment stored for an index and the commitment actually published to Bitcoin stop being the same object; the adjacent symbols in the same file that carry the value are `CommitmentService`, `run`, `commit`, `store_commitments_from_da`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: stored commitments match published ones
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: publish then overwrite and assert the stored value tracks Bitcoin
