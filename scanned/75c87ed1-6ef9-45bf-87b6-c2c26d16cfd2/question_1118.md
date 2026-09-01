# Q1118: deposit dedup keyed on attacker bytes via `eth_syncing` (rpc.rs)

## Question
Can an unprivileged attacker who submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height, controlling the ABI encoding of the wrapped Bridge argument, drive `eth_syncing` in `crates/sequencer/src/rpc.rs` so that the txid `calc_tx_id` derives from the attacker's blob and the Bitcoin txid the Bridge contract will credit stop being the same identifier, breaking the invariant that one Bitcoin move-to-vault output maps to exactly one admitted deposit blob?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `eth_syncing`
- Entrypoint: unprivileged party submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height
- Attacker controls: the ABI encoding of the wrapped Bridge argument
- Exploit idea: deposit dedup keyed on attacker bytes - reach `eth_syncing` from that entrypoint and force the divergence where the txid `calc_tx_id` derives from the attacker's blob and the Bitcoin txid the Bridge contract will credit stop being the same identifier; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: one Bitcoin move-to-vault output maps to exactly one admitted deposit blob
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: submit two blobs with the same derived txid but different bodies and assert only one survives `remove_deposits`
