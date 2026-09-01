# Q1998: deposit blob type confusion via `eth_send_raw_transaction` (rpc.rs)

## Question
Can an unprivileged attacker who calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob, controlling the entire `Bytes` deposit payload, drive `eth_send_raw_transaction` in `crates/sequencer/src/rpc.rs` so that the selector the sequencer wraps the blob in and the selector the Bridge contract dispatches on stop being the same function, breaking the invariant that the deposit blob is only ever interpreted as a Bridge deposit argument?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `eth_send_raw_transaction`
- Entrypoint: unprivileged party calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob
- Attacker controls: the entire `Bytes` deposit payload
- Exploit idea: deposit blob type confusion - reach `eth_send_raw_transaction` from that entrypoint and force the divergence where the selector the sequencer wraps the blob in and the selector the Bridge contract dispatches on stop being the same function; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the deposit blob is only ever interpreted as a Bridge deposit argument
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: submit a blob that ABI-decodes as a different Bridge method and assert it is rejected
