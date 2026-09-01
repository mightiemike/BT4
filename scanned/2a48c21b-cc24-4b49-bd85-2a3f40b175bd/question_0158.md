# Q0158: oversized blob accepted via `calc_tx_id` (deposit_data_mempool.rs)

## Question
Can an unprivileged attacker who submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height, controlling the `calc_tx_id` preimage, drive `calc_tx_id` in `crates/sequencer/src/deposit_data_mempool.rs` so that the blob length the sequencer admits and the length the Bridge call can encode stop being compatible, breaking the invariant that an admitted deposit is always encodable into a block?

## Target
- File/function: `crates/sequencer/src/deposit_data_mempool.rs` -> `calc_tx_id`
- Entrypoint: unprivileged party submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height
- Attacker controls: the `calc_tx_id` preimage
- Exploit idea: oversized blob accepted - reach `calc_tx_id` from that entrypoint and force the divergence where the blob length the sequencer admits and the length the Bridge call can encode stop being compatible; the adjacent symbols in the same file that carry the value are `DepositDataMempool`, `make_deposit_tx_from_data`, `fetch_deposits`, `remove_deposits`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: an admitted deposit is always encodable into a block
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: submit a maximal blob and assert block production still completes
