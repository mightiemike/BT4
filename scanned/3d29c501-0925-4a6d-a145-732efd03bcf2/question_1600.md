# Q1600: mempool ordering determinism via `pending_block_and_receipts` (mod.rs)

## Question
Can an unprivileged attacker who submits a burst of transactions while the sequencer is sealing a block, controlling submission timing, drive `pending_block_and_receipts` in `crates/sequencer/src/db_provider/mod.rs` so that the transaction order the sequencer executes and the order the stored block records stop being the same order, breaking the invariant that the stored block reproduces the executed order?

## Target
- File/function: `crates/sequencer/src/db_provider/mod.rs` -> `pending_block_and_receipts`
- Entrypoint: unprivileged party submits a burst of transactions while the sequencer is sealing a block
- Attacker controls: submission timing
- Exploit idea: mempool ordering determinism - reach `pending_block_and_receipts` from that entrypoint and force the divergence where the transaction order the sequencer executes and the order the stored block records stop being the same order; the adjacent symbols in the same file that carry the value are `DbProvider`, `cfg`, `last_block_tx_hashes`, `last_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the stored block reproduces the executed order
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: replay the stored block and diff receipts
