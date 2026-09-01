# Q1628: deposit dedup keyed on attacker bytes via `calc_tx_id` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies, controlling the `calc_tx_id` preimage, drive `calc_tx_id` in `crates/sequencer/src/deposit_data_mempool.rs` so that the txid `calc_tx_id` derives from the attacker's blob and the Bitcoin txid the Bridge contract will credit stop being the same identifier, breaking the invariant that one Bitcoin move-to-vault output maps to exactly one admitted deposit blob?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `calc_tx_id`
- Entrypoint: unprivileged party submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies
- Attacker controls: the `calc_tx_id` preimage
- Exploit idea: deposit dedup keyed on attacker bytes - reach `calc_tx_id` from that entrypoint and force the divergence where the txid `calc_tx_id` derives from the attacker's blob and the Bitcoin txid the Bridge contract will credit stop being the same identifier; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `remove_deposits`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: one Bitcoin move-to-vault output maps to exactly one admitted deposit blob
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: submit two blobs with the same derived txid but different bodies and assert only one survives `remove_deposits`
