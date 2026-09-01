# Q3729: mempool ordering determinism via `create_chain_notification` (runner.rs)

## Question
Can an unprivileged attacker who submits a burst of transactions while the sequencer is sealing a block, controlling the number and size of transactions per block, drive `create_chain_notification` in `crates/sequencer/src/runner.rs` so that the transaction order the sequencer executes and the order the stored block records stop being the same order, breaking the invariant that the stored block reproduces the executed order?

## Target
- File/function: `crates/sequencer/src/runner.rs` -> `create_chain_notification`
- Entrypoint: unprivileged party submits a burst of transactions while the sequencer is sealing a block
- Attacker controls: the number and size of transactions per block
- Exploit idea: mempool ordering determinism - reach `create_chain_notification` from that entrypoint and force the divergence where the transaction order the sequencer executes and the order the stored block records stop being the same order; the adjacent symbols in the same file that carry the value are `CitreaSequencer`, `dry_run_transactions`, `save_short_header_proofs`, `produce_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the stored block reproduces the executed order
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: replay the stored block and diff receipts
