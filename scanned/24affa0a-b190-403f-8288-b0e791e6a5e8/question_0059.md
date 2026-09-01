# Q0059: simulation/execution gas divergence via `remove_deposits` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who resubmits a deposit blob that was already included in an earlier L2 block, controlling submission timing relative to block sealing, drive `remove_deposits` in `crates/sequencer/src/deposit_data_mempool.rs` so that the gas the admission simulation charged and the gas the block execution charges stop being the same, breaking the invariant that admission implies executability under `SYSTEM_TX_GAS_LIMIT`?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `remove_deposits`
- Entrypoint: unprivileged party resubmits a deposit blob that was already included in an earlier L2 block
- Attacker controls: submission timing relative to block sealing
- Exploit idea: simulation/execution gas divergence - reach `remove_deposits` from that entrypoint and force the divergence where the gas the admission simulation charged and the gas the block execution charges stop being the same; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: admission implies executability under `SYSTEM_TX_GAS_LIMIT`
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: craft a blob whose gas use grows with state and assert inclusion still succeeds
