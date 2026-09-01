# Q5084: mempool ordering determinism via `dry_run_transactions` (runner.rs)

## Question
Can an unprivileged attacker who submits deposits and ordinary transactions that compete for the same block space, controlling submission timing, drive `dry_run_transactions` in `crates/sequencer/src/runner.rs` so that the transaction order the sequencer executes and the order the stored block records stop being the same order, breaking the invariant that the stored block reproduces the executed order?

## Target
- File/function: `crates/sequencer/src/runner.rs` -> `dry_run_transactions`
- Entrypoint: unprivileged party submits deposits and ordinary transactions that compete for the same block space
- Attacker controls: submission timing
- Exploit idea: mempool ordering determinism - reach `dry_run_transactions` from that entrypoint and force the divergence where the transaction order the sequencer executes and the order the stored block records stop being the same order; the adjacent symbols in the same file that carry the value are `CitreaSequencer`, `save_short_header_proofs`, `produce_l2_block`, `produce_l2_block_inner`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the stored block reproduces the executed order
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: replay the stored block and diff receipts
