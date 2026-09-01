# Q1808: system-signer nonce coupling via `add_deposit_tx` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob, controlling the number of competing blobs queued, drive `add_deposit_tx` in `crates/sequencer/src/deposit_data_mempool.rs` so that the nonce the deposit system transaction is built with and the nonce the EVM expects for `SYSTEM_SIGNER` stop matching, breaking the invariant that system transactions never collide with user transactions in nonce space?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `add_deposit_tx`
- Entrypoint: unprivileged party calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob
- Attacker controls: the number of competing blobs queued
- Exploit idea: system-signer nonce coupling - reach `add_deposit_tx` from that entrypoint and force the divergence where the nonce the deposit system transaction is built with and the nonce the EVM expects for `SYSTEM_SIGNER` stop matching; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `remove_deposits`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: system transactions never collide with user transactions in nonce space
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: interleave user transactions with deposits and assert every system transaction executes
