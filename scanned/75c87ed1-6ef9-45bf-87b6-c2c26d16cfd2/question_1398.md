# Q1398: deposit replay across blocks via `eth_get_raw_transaction_by_hash` (rpc.rs)

## Question
Can an unprivileged attacker who submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan, controlling the entire `Bytes` deposit payload, drive `eth_get_raw_transaction_by_hash` in `crates/sequencer/src/rpc.rs` so that the number of times a given deposit blob is executed as a `SYSTEM_SIGNER` transaction and the number of times it was funded on Bitcoin stop being equal, breaking the invariant that each deposit is minted exactly once?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `eth_get_raw_transaction_by_hash`
- Entrypoint: unprivileged party submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan
- Attacker controls: the entire `Bytes` deposit payload
- Exploit idea: deposit replay across blocks - reach `eth_get_raw_transaction_by_hash` from that entrypoint and force the divergence where the number of times a given deposit blob is executed as a `SYSTEM_SIGNER` transaction and the number of times it was funded on Bitcoin stop being equal; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each deposit is minted exactly once
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: resubmit an already-included blob and assert the second inclusion reverts
