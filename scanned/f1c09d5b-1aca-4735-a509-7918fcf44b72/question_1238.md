# Q1238: deposit displacement / starvation via `remove_deposits` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who resubmits a deposit blob that was already included in an earlier L2 block, controlling the number of competing blobs queued, drive `remove_deposits` in `crates/sequencer/src/deposit_data_mempool.rs` so that the set of deposits `fetch_deposits` returns and the set of real pending Bitcoin deposits stop being the same set, breaking the invariant that every valid Bitcoin deposit eventually reaches an L2 block?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `remove_deposits`
- Entrypoint: unprivileged party resubmits a deposit blob that was already included in an earlier L2 block
- Attacker controls: the number of competing blobs queued
- Exploit idea: deposit displacement / starvation - reach `remove_deposits` from that entrypoint and force the divergence where the set of deposits `fetch_deposits` returns and the set of real pending Bitcoin deposits stop being the same set; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every valid Bitcoin deposit eventually reaches an L2 block
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: fill the queue with attacker blobs and assert a legitimate deposit is still included within N blocks
