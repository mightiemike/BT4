# Q3659: db provider view during build via `dry_run_transactions` (runner.rs)

## Question
Can an unprivileged attacker who submits a burst of transactions while the sequencer is sealing a block, controlling submission timing, drive `dry_run_transactions` in `crates/sequencer/src/runner.rs` so that the state the block builder reads and the state the block finally commits stop being the same, breaking the invariant that block building reads the state it commits to?

## Target
- File/function: `crates/sequencer/src/runner.rs` -> `dry_run_transactions`
- Entrypoint: unprivileged party submits a burst of transactions while the sequencer is sealing a block
- Attacker controls: submission timing
- Exploit idea: db provider view during build - reach `dry_run_transactions` from that entrypoint and force the divergence where the state the block builder reads and the state the block finally commits stop being the same; the adjacent symbols in the same file that carry the value are `CitreaSequencer`, `save_short_header_proofs`, `produce_l2_block`, `produce_l2_block_inner`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block building reads the state it commits to
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: build under concurrent state changes and diff
