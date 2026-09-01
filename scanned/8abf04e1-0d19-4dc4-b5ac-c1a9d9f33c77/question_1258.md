# Q1258: system-signer nonce coupling via `make_deposit_tx_from_data` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan, controlling the number of competing blobs queued, drive `make_deposit_tx_from_data` in `crates/sequencer/src/deposit_data_mempool.rs` so that the nonce the deposit system transaction is built with and the nonce the EVM expects for `SYSTEM_SIGNER` stop matching, breaking the invariant that system transactions never collide with user transactions in nonce space?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `make_deposit_tx_from_data`
- Entrypoint: unprivileged party submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan
- Attacker controls: the number of competing blobs queued
- Exploit idea: system-signer nonce coupling - reach `make_deposit_tx_from_data` from that entrypoint and force the divergence where the nonce the deposit system transaction is built with and the nonce the EVM expects for `SYSTEM_SIGNER` stop matching; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `fetch_deposits`, `remove_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: system transactions never collide with user transactions in nonce space
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: interleave user transactions with deposits and assert every system transaction executes
