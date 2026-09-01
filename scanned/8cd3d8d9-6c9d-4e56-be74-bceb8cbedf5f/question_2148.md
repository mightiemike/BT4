# Q2148: deposit blob type confusion via `resume_commitments` (rpc.rs)

## Question
Can an unprivileged attacker who submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies, controlling the `calc_tx_id` preimage, drive `resume_commitments` in `crates/sequencer/src/rpc.rs` so that the selector the sequencer wraps the blob in and the selector the Bridge contract dispatches on stop being the same function, breaking the invariant that the deposit blob is only ever interpreted as a Bridge deposit argument?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `resume_commitments`
- Entrypoint: unprivileged party submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies
- Attacker controls: the `calc_tx_id` preimage
- Exploit idea: deposit blob type confusion - reach `resume_commitments` from that entrypoint and force the divergence where the selector the sequencer wraps the blob in and the selector the Bridge contract dispatches on stop being the same function; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the deposit blob is only ever interpreted as a Bridge deposit argument
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: submit a blob that ABI-decodes as a different Bridge method and assert it is rejected
