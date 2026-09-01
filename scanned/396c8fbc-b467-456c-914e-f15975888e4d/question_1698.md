# Q1698: deposit blob type confusion via `make_deposit_tx_from_data` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan, controlling the ABI encoding of the wrapped Bridge argument, drive `make_deposit_tx_from_data` in `crates/sequencer/src/deposit_data_mempool.rs` so that the selector the sequencer wraps the blob in and the selector the Bridge contract dispatches on stop being the same function, breaking the invariant that the deposit blob is only ever interpreted as a Bridge deposit argument?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `make_deposit_tx_from_data`
- Entrypoint: unprivileged party submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan
- Attacker controls: the ABI encoding of the wrapped Bridge argument
- Exploit idea: deposit blob type confusion - reach `make_deposit_tx_from_data` from that entrypoint and force the divergence where the selector the sequencer wraps the blob in and the selector the Bridge contract dispatches on stop being the same function; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `fetch_deposits`, `remove_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the deposit blob is only ever interpreted as a Bridge deposit argument
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: submit a blob that ABI-decodes as a different Bridge method and assert it is rejected
