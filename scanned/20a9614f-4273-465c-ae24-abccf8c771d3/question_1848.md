# Q1848: deposit blob type confusion via `remove_deposits` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies, controlling the number of competing blobs queued, drive `remove_deposits` in `crates/sequencer/src/deposit_data_mempool.rs` so that the selector the sequencer wraps the blob in and the selector the Bridge contract dispatches on stop being the same function, breaking the invariant that the deposit blob is only ever interpreted as a Bridge deposit argument?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `remove_deposits`
- Entrypoint: unprivileged party submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies
- Attacker controls: the number of competing blobs queued
- Exploit idea: deposit blob type confusion - reach `remove_deposits` from that entrypoint and force the divergence where the selector the sequencer wraps the blob in and the selector the Bridge contract dispatches on stop being the same function; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the deposit blob is only ever interpreted as a Bridge deposit argument
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: submit a blob that ABI-decodes as a different Bridge method and assert it is rejected
