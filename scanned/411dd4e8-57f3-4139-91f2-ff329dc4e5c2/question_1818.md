# Q1818: simulation/execution gas divergence via `add_deposit_tx` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan, controlling the `calc_tx_id` preimage, drive `add_deposit_tx` in `crates/sequencer/src/deposit_data_mempool.rs` so that the gas the admission simulation charged and the gas the block execution charges stop being the same, breaking the invariant that admission implies executability under `SYSTEM_TX_GAS_LIMIT`?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `add_deposit_tx`
- Entrypoint: unprivileged party submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan
- Attacker controls: the `calc_tx_id` preimage
- Exploit idea: simulation/execution gas divergence - reach `add_deposit_tx` from that entrypoint and force the divergence where the gas the admission simulation charged and the gas the block execution charges stop being the same; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `remove_deposits`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission implies executability under `SYSTEM_TX_GAS_LIMIT`
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: craft a blob whose gas use grows with state and assert inclusion still succeeds
