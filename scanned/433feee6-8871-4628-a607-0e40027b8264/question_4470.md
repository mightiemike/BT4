# Q4470: block-by-hash for a non-canonical block via `get_verified_batch_proofs_by_slot_height` (server.rs)

## Question
Can an unprivileged attacker who asks a node for a proof or commitment it has stored but not yet verified, controlling range and index parameters, drive `get_verified_batch_proofs_by_slot_height` in `crates/sovereign-sdk/full-node/sov-ledger-rpc/src/server.rs` so that the block a hash lookup returns and the canonical block at that height stop being the same block, breaking the invariant that hash lookups never surface orphaned state as canonical?

## Target
- File/function: `crates/sovereign-sdk/full-node/sov-ledger-rpc/src/server.rs` -> `get_verified_batch_proofs_by_slot_height`
- Entrypoint: unprivileged party asks a node for a proof or commitment it has stored but not yet verified
- Attacker controls: range and index parameters
- Exploit idea: block-by-hash for a non-canonical block - reach `get_verified_batch_proofs_by_slot_height` from that entrypoint and force the divergence where the block a hash lookup returns and the canonical block at that height stop being the same block; the adjacent symbols in the same file that carry the value are `LedgerRpcServerConfig`, `LedgerRpcServerImpl`, `to_ledger_rpc_error`, `get_l2_block_by_number`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: hash lookups never surface orphaned state as canonical
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: insert a non-canonical block and assert lookups mark it as such
