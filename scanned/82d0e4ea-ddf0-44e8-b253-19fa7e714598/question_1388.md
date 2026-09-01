# Q1388: deposit displacement / starvation via `eth_send_raw_transaction` (rpc.rs)

## Question
Can an unprivileged attacker who submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies, controlling submission timing relative to block sealing, drive `eth_send_raw_transaction` in `crates/sequencer/src/rpc.rs` so that the set of deposits `fetch_deposits` returns and the set of real pending Bitcoin deposits stop being the same set, breaking the invariant that every valid Bitcoin deposit eventually reaches an L2 block?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `eth_send_raw_transaction`
- Entrypoint: unprivileged party submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies
- Attacker controls: submission timing relative to block sealing
- Exploit idea: deposit displacement / starvation - reach `eth_send_raw_transaction` from that entrypoint and force the divergence where the set of deposits `fetch_deposits` returns and the set of real pending Bitcoin deposits stop being the same set; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every valid Bitcoin deposit eventually reaches an L2 block
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: fill the queue with attacker blobs and assert a legitimate deposit is still included within N blocks
