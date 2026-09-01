# Q0658: system-signer nonce coupling via `calc_tx_id` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies, controlling the `calc_tx_id` preimage, drive `calc_tx_id` in `crates/sequencer/src/deposit_data_mempool.rs` so that the nonce the deposit system transaction is built with and the nonce the EVM expects for `SYSTEM_SIGNER` stop matching, breaking the invariant that system transactions never collide with user transactions in nonce space?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `calc_tx_id`
- Entrypoint: unprivileged party submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies
- Attacker controls: the `calc_tx_id` preimage
- Exploit idea: system-signer nonce coupling - reach `calc_tx_id` from that entrypoint and force the divergence where the nonce the deposit system transaction is built with and the nonce the EVM expects for `SYSTEM_SIGNER` stop matching; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `remove_deposits`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: system transactions never collide with user transactions in nonce space
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: interleave user transactions with deposits and assert every system transaction executes
