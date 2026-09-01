# Q1508: deposit accepted for an unconfirmed Bitcoin tx via `txpool_remove_txs_by_sender` (rpc.rs)

## Question
Can an unprivileged attacker who calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob, controlling submission timing relative to block sealing, drive `txpool_remove_txs_by_sender` in `crates/sequencer/src/rpc.rs` so that the Bitcoin confirmation depth the blob implies and the depth the bridge requires stop being the same, breaking the invariant that deposits mint only against sufficiently confirmed Bitcoin outputs?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `txpool_remove_txs_by_sender`
- Entrypoint: unprivileged party calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob
- Attacker controls: submission timing relative to block sealing
- Exploit idea: deposit accepted for an unconfirmed Bitcoin tx - reach `txpool_remove_txs_by_sender` from that entrypoint and force the divergence where the Bitcoin confirmation depth the blob implies and the depth the bridge requires stop being the same; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: deposits mint only against sufficiently confirmed Bitcoin outputs
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: submit a blob for a shallow/orphaned tx and assert rejection
