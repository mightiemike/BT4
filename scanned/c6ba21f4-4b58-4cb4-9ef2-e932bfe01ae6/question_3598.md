# Q3598: proof RPC exposing unverified data via `get_verified_batch_proofs_by_slot_height` (server.rs)

## Question
Can an unprivileged attacker who calls a ledger / node RPC method with out-of-range or reversed parameters, controlling the height or index requested, drive `get_verified_batch_proofs_by_slot_height` in `crates/sovereign-sdk/full-node/sov-ledger-rpc/src/server.rs` so that the proof data an RPC returns and the proof the node actually verified stop being the same artefact, breaking the invariant that a node only serves proofs it verified?

## Target
- File/function: `crates/sovereign-sdk/full-node/sov-ledger-rpc/src/server.rs` -> `get_verified_batch_proofs_by_slot_height`
- Entrypoint: unprivileged party calls a ledger / node RPC method with out-of-range or reversed parameters
- Attacker controls: the height or index requested
- Exploit idea: proof RPC exposing unverified data - reach `get_verified_batch_proofs_by_slot_height` from that entrypoint and force the divergence where the proof data an RPC returns and the proof the node actually verified stop being the same artefact; the adjacent symbols in the same file that carry the value are `LedgerRpcServerConfig`, `LedgerRpcServerImpl`, `to_ledger_rpc_error`, `get_l2_block_by_number`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a node only serves proofs it verified
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: serve a stored-but-unverified proof and assert the RPC refuses it
