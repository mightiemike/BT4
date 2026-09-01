# Q1868: removal by txid over-matching via `remove_deposits` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies, controlling the ABI encoding of the wrapped Bridge argument, drive `remove_deposits` in `crates/sequencer/src/deposit_data_mempool.rs` so that the deposits `remove_deposits` erases and the deposits actually included in the block stop being the same set, breaking the invariant that only included deposits leave the mempool?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `remove_deposits`
- Entrypoint: unprivileged party submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies
- Attacker controls: the ABI encoding of the wrapped Bridge argument
- Exploit idea: removal by txid over-matching - reach `remove_deposits` from that entrypoint and force the divergence where the deposits `remove_deposits` erases and the deposits actually included in the block stop being the same set; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only included deposits leave the mempool
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: include one deposit whose txid collides with a pending one and assert the pending one survives
