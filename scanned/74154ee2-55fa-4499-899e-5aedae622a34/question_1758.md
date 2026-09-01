# Q1758: deposit accepted for an unconfirmed Bitcoin tx via `remove_deposits` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who resubmits a deposit blob that was already included in an earlier L2 block, controlling the number of competing blobs queued, drive `remove_deposits` in `crates/sequencer/src/deposit_data_mempool.rs` so that the Bitcoin confirmation depth the blob implies and the depth the bridge requires stop being the same, breaking the invariant that deposits mint only against sufficiently confirmed Bitcoin outputs?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `remove_deposits`
- Entrypoint: unprivileged party resubmits a deposit blob that was already included in an earlier L2 block
- Attacker controls: the number of competing blobs queued
- Exploit idea: deposit accepted for an unconfirmed Bitcoin tx - reach `remove_deposits` from that entrypoint and force the divergence where the Bitcoin confirmation depth the blob implies and the depth the bridge requires stop being the same; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: deposits mint only against sufficiently confirmed Bitcoin outputs
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: submit a blob for a shallow/orphaned tx and assert rejection
