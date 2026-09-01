# Q0842: unverified data persisted as canonical via `insert_pending_commitment_if_not_exists` (da_block_handler.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that a syncing full node must process before it has the matching proof, controlling what a syncing node sees first, drive `insert_pending_commitment_if_not_exists` in `crates/fullnode/src/da_block_handler.rs` so that the data a node persists and the data it has verified stop being the same set, breaking the invariant that only verified data becomes canonical?

## Target
- File/function: `crates/fullnode/src/da_block_handler.rs` -> `insert_pending_commitment_if_not_exists`
- Entrypoint: unprivileged party inscribes L1 data that a syncing full node must process before it has the matching proof
- Attacker controls: what a syncing node sees first
- Exploit idea: unverified data persisted as canonical - reach `insert_pending_commitment_if_not_exists` from that entrypoint and force the divergence where the data a node persists and the data it has verified stop being the same set; the adjacent symbols in the same file that carry the value are `ProcessingResult`, `ProofSource`, `L1BlockHandler`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only verified data becomes canonical
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: persist a pending proof and assert it is not served as final
