# Q1908: deposit accepted for an unconfirmed Bitcoin tx via `eth_get_transaction_by_hash` (rpc.rs)

## Question
Can an unprivileged attacker who calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob, controlling the entire `Bytes` deposit payload, drive `eth_get_transaction_by_hash` in `crates/sequencer/src/rpc.rs` so that the Bitcoin confirmation depth the blob implies and the depth the bridge requires stop being the same, breaking the invariant that deposits mint only against sufficiently confirmed Bitcoin outputs?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `eth_get_transaction_by_hash`
- Entrypoint: unprivileged party calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob
- Attacker controls: the entire `Bytes` deposit payload
- Exploit idea: deposit accepted for an unconfirmed Bitcoin tx - reach `eth_get_transaction_by_hash` from that entrypoint and force the divergence where the Bitcoin confirmation depth the blob implies and the depth the bridge requires stop being the same; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: deposits mint only against sufficiently confirmed Bitcoin outputs
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: submit a blob for a shallow/orphaned tx and assert rejection
