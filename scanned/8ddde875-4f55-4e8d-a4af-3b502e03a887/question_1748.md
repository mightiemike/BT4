# Q1748: pending_deposits set desync via `fetch_deposits` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height, controlling the number of competing blobs queued, drive `fetch_deposits` in `crates/sequencer/src/deposit_data_mempool.rs` so that the set of pending deposit txids and the deposits actually queued stop being the same set, breaking the invariant that the dedup set mirrors the queue?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `fetch_deposits`
- Entrypoint: unprivileged party submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height
- Attacker controls: the number of competing blobs queued
- Exploit idea: pending_deposits set desync - reach `fetch_deposits` from that entrypoint and force the divergence where the set of pending deposit txids and the deposits actually queued stop being the same set; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `remove_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the dedup set mirrors the queue
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: drive add/remove races and assert set equality
