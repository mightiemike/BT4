# Q0048: system-signer nonce coupling via `remove_deposits` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who resubmits a deposit blob that was already included in an earlier L2 block, controlling the entire `Bytes` deposit payload, drive `remove_deposits` in `crates/sequencer/src/deposit_data_mempool.rs` so that the nonce the deposit system transaction is built with and the nonce the EVM expects for `SYSTEM_SIGNER` stop matching, breaking the invariant that system transactions never collide with user transactions in nonce space?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `remove_deposits`
- Entrypoint: unprivileged party resubmits a deposit blob that was already included in an earlier L2 block
- Attacker controls: the entire `Bytes` deposit payload
- Exploit idea: system-signer nonce coupling - reach `remove_deposits` from that entrypoint and force the divergence where the nonce the deposit system transaction is built with and the nonce the EVM expects for `SYSTEM_SIGNER` stop matching; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: system transactions never collide with user transactions in nonce space
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: interleave user transactions with deposits and assert every system transaction executes
