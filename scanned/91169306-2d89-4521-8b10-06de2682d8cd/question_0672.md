# Q0672: cache coherence with storage via `shutdown_requested` (utils.rs)

## Question
Can an unprivileged attacker who drives a node's caches with queries for state it is concurrently updating, controlling the block tags requested, drive `shutdown_requested` in `crates/common/src/utils.rs` so that the value served from cache and the value in storage stop being the same, breaking the invariant that caches never diverge from storage?

## Target
- File/function: `crates/common/src/utils.rs` -> `shutdown_requested`
- Entrypoint: unprivileged party drives a node's caches with queries for state it is concurrently updating
- Attacker controls: the block tags requested
- Exploit idea: cache coherence with storage - reach `shutdown_requested` from that entrypoint and force the divergence where the value served from cache and the value in storage stop being the same; the adjacent symbols in the same file that carry the value are `merge_state_diffs`, `check_l2_block_exists`, `update_short_header_proof_from_sys_tx`, `decode_sov_tx_and_update_short_header_proofs`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: caches never diverge from storage
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: invalidate under load and diff
