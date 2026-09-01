# Q2581: mempool ordering determinism via `encode_and_sign_evm_txs_into_sov_txs` (runner.rs)

## Question
Can an unprivileged attacker who submits deposits and ordinary transactions that compete for the same block space, controlling the number and size of transactions per block, drive `encode_and_sign_evm_txs_into_sov_txs` in `crates/sequencer/src/runner.rs` so that the transaction order the sequencer executes and the order the stored block records stop being the same order, breaking the invariant that the stored block reproduces the executed order?

## Target
- File/function: `crates/sequencer/src/runner.rs` -> `encode_and_sign_evm_txs_into_sov_txs`
- Entrypoint: unprivileged party submits deposits and ordinary transactions that compete for the same block space
- Attacker controls: the number and size of transactions per block
- Exploit idea: mempool ordering determinism - reach `encode_and_sign_evm_txs_into_sov_txs` from that entrypoint and force the divergence where the transaction order the sequencer executes and the order the stored block records stop being the same order; the adjacent symbols in the same file that carry the value are `CitreaSequencer`, `dry_run_transactions`, `save_short_header_proofs`, `produce_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the stored block reproduces the executed order
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: replay the stored block and diff receipts
