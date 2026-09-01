# Q1718: removal by txid over-matching via `make_deposit_tx_from_data` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies, controlling submission timing relative to block sealing, drive `make_deposit_tx_from_data` in `crates/sequencer/src/deposit_data_mempool.rs` so that the deposits `remove_deposits` erases and the deposits actually included in the block stop being the same set, breaking the invariant that only included deposits leave the mempool?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `make_deposit_tx_from_data`
- Entrypoint: unprivileged party submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies
- Attacker controls: submission timing relative to block sealing
- Exploit idea: removal by txid over-matching - reach `make_deposit_tx_from_data` from that entrypoint and force the divergence where the deposits `remove_deposits` erases and the deposits actually included in the block stop being the same set; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `fetch_deposits`, `remove_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only included deposits leave the mempool
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: include one deposit whose txid collides with a pending one and assert the pending one survives
