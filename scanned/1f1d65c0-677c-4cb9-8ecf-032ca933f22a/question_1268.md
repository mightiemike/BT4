# Q1268: simulation/execution gas divergence via `make_deposit_tx_from_data` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan, controlling the number of competing blobs queued, drive `make_deposit_tx_from_data` in `crates/sequencer/src/deposit_data_mempool.rs` so that the gas the admission simulation charged and the gas the block execution charges stop being the same, breaking the invariant that admission implies executability under `SYSTEM_TX_GAS_LIMIT`?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `make_deposit_tx_from_data`
- Entrypoint: unprivileged party submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan
- Attacker controls: the number of competing blobs queued
- Exploit idea: simulation/execution gas divergence - reach `make_deposit_tx_from_data` from that entrypoint and force the divergence where the gas the admission simulation charged and the gas the block execution charges stop being the same; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `fetch_deposits`, `remove_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission implies executability under `SYSTEM_TX_GAS_LIMIT`
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: craft a blob whose gas use grows with state and assert inclusion still succeeds
