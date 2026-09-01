# Q1128: deposit displacement / starvation via `txpool_remove_txs_by_hash` (rpc.rs)

## Question
Can an unprivileged attacker who resubmits a deposit blob that was already included in an earlier L2 block, controlling submission timing relative to block sealing, drive `txpool_remove_txs_by_hash` in `crates/sequencer/src/rpc.rs` so that the set of deposits `fetch_deposits` returns and the set of real pending Bitcoin deposits stop being the same set, breaking the invariant that every valid Bitcoin deposit eventually reaches an L2 block?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `txpool_remove_txs_by_hash`
- Entrypoint: unprivileged party resubmits a deposit blob that was already included in an earlier L2 block
- Attacker controls: submission timing relative to block sealing
- Exploit idea: deposit displacement / starvation - reach `txpool_remove_txs_by_hash` from that entrypoint and force the divergence where the set of deposits `fetch_deposits` returns and the set of real pending Bitcoin deposits stop being the same set; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every valid Bitcoin deposit eventually reaches an L2 block
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: fill the queue with attacker blobs and assert a legitimate deposit is still included within N blocks
