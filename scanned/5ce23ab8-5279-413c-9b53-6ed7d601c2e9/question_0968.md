# Q0968: simulation/execution gas divergence via `calc_tx_id` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who resubmits a deposit blob that was already included in an earlier L2 block, controlling the `calc_tx_id` preimage, drive `calc_tx_id` in `crates/sequencer/src/deposit_data_mempool.rs` so that the gas the admission simulation charged and the gas the block execution charges stop being the same, breaking the invariant that admission implies executability under `SYSTEM_TX_GAS_LIMIT`?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `calc_tx_id`
- Entrypoint: unprivileged party resubmits a deposit blob that was already included in an earlier L2 block
- Attacker controls: the `calc_tx_id` preimage
- Exploit idea: simulation/execution gas divergence - reach `calc_tx_id` from that entrypoint and force the divergence where the gas the admission simulation charged and the gas the block execution charges stop being the same; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `remove_deposits`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission implies executability under `SYSTEM_TX_GAS_LIMIT`
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: craft a blob whose gas use grows with state and assert inclusion still succeeds
