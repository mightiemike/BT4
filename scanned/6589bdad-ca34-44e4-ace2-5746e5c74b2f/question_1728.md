# Q1728: oversized blob accepted via `make_deposit_tx_from_data` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan, controlling the entire `Bytes` deposit payload, drive `make_deposit_tx_from_data` in `crates/sequencer/src/deposit_data_mempool.rs` so that the blob length the sequencer admits and the length the Bridge call can encode stop being compatible, breaking the invariant that an admitted deposit is always encodable into a block?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `make_deposit_tx_from_data`
- Entrypoint: unprivileged party submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan
- Attacker controls: the entire `Bytes` deposit payload
- Exploit idea: oversized blob accepted - reach `make_deposit_tx_from_data` from that entrypoint and force the divergence where the blob length the sequencer admits and the length the Bridge call can encode stop being compatible; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `fetch_deposits`, `remove_deposits`, `add_deposit_tx`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: an admitted deposit is always encodable into a block
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: submit a maximal blob and assert block production still completes
