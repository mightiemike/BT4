# Q1028: queue ordering versus fee market via `eth_get_transaction_by_hash` (rpc.rs)

## Question
Can an unprivileged attacker who calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob, controlling submission timing relative to block sealing, drive `eth_get_transaction_by_hash` in `crates/sequencer/src/rpc.rs` so that the deposit ordering the sequencer commits to and the ordering the Bitcoin side established stop being the same order, breaking the invariant that deposit credit order does not change which deposits succeed?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `eth_get_transaction_by_hash`
- Entrypoint: unprivileged party calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob
- Attacker controls: submission timing relative to block sealing
- Exploit idea: queue ordering versus fee market - reach `eth_get_transaction_by_hash` from that entrypoint and force the divergence where the deposit ordering the sequencer commits to and the ordering the Bitcoin side established stop being the same order; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: deposit credit order does not change which deposits succeed
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: reorder the FIFO queue under attacker load and assert all deposits still mint
