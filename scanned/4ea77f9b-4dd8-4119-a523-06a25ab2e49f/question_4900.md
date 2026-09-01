# Q4900: proof RPC exposing unverified data via `check_if_l2_block_pruned` (rpc.rs)

## Question
Can an unprivileged attacker who asks a node for a proof or commitment it has stored but not yet verified, controlling the height or index requested, drive `check_if_l2_block_pruned` in `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` so that the proof data an RPC returns and the proof the node actually verified stop being the same artefact, breaking the invariant that a node only serves proofs it verified?

## Target
- File/function: `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` -> `check_if_l2_block_pruned`
- Entrypoint: unprivileged party asks a node for a proof or commitment it has stored but not yet verified
- Attacker controls: the height or index requested
- Exploit idea: proof RPC exposing unverified data - reach `check_if_l2_block_pruned` from that entrypoint and force the divergence where the proof data an RPC returns and the proof the node actually verified stop being the same artefact; the adjacent symbols in the same file that carry the value are `get_l2_block`, `get_l2_block_by_hash`, `get_l2_block_by_number`, `get_l2_blocks_range`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a node only serves proofs it verified
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: serve a stored-but-unverified proof and assert the RPC refuses it
