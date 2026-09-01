# Q1668: simulation/execution gas divergence via `fetch_deposits` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies, controlling submission timing relative to block sealing, drive `fetch_deposits` in `crates/sequencer/src/deposit_data_mempool.rs` so that the gas the admission simulation charged and the gas the block execution charges stop being the same, breaking the invariant that admission implies executability under `SYSTEM_TX_GAS_LIMIT`?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `fetch_deposits`
- Entrypoint: unprivileged party submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies
- Attacker controls: submission timing relative to block sealing
- Exploit idea: simulation/execution gas divergence - reach `fetch_deposits` from that entrypoint and force the divergence where the gas the admission simulation charged and the gas the block execution charges stop being the same; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `remove_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission implies executability under `SYSTEM_TX_GAS_LIMIT`
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: craft a blob whose gas use grows with state and assert inclusion still succeeds
