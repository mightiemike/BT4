# Q1248: deposit replay across blocks via `remove_deposits` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies, controlling the entire `Bytes` deposit payload, drive `remove_deposits` in `crates/sequencer/src/deposit_data_mempool.rs` so that the number of times a given deposit blob is executed as a `SYSTEM_SIGNER` transaction and the number of times it was funded on Bitcoin stop being equal, breaking the invariant that each deposit is minted exactly once?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `remove_deposits`
- Entrypoint: unprivileged party submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies
- Attacker controls: the entire `Bytes` deposit payload
- Exploit idea: deposit replay across blocks - reach `remove_deposits` from that entrypoint and force the divergence where the number of times a given deposit blob is executed as a `SYSTEM_SIGNER` transaction and the number of times it was funded on Bitcoin stop being equal; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each deposit is minted exactly once
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: resubmit an already-included blob and assert the second inclusion reverts
