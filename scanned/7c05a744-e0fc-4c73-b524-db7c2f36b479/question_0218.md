# Q0218: deposit dedup keyed on attacker bytes via `eth_get_transaction_by_hash` (rpc.rs)

## Question
Can an unprivileged attacker who resubmits a deposit blob that was already included in an earlier L2 block, controlling the entire `Bytes` deposit payload, drive `eth_get_transaction_by_hash` in `crates/sequencer/src/rpc.rs` so that the txid `calc_tx_id` derives from the attacker's blob and the Bitcoin txid the Bridge contract will credit stop being the same identifier, breaking the invariant that one Bitcoin move-to-vault output maps to exactly one admitted deposit blob?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `eth_get_transaction_by_hash`
- Entrypoint: unprivileged party resubmits a deposit blob that was already included in an earlier L2 block
- Attacker controls: the entire `Bytes` deposit payload
- Exploit idea: deposit dedup keyed on attacker bytes - reach `eth_get_transaction_by_hash` from that entrypoint and force the divergence where the txid `calc_tx_id` derives from the attacker's blob and the Bitcoin txid the Bridge contract will credit stop being the same identifier; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: one Bitcoin move-to-vault output maps to exactly one admitted deposit blob
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: submit two blobs with the same derived txid but different bodies and assert only one survives `remove_deposits`
