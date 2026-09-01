# Q2635: db provider view during build via `extract_receipts_from_working_set` (runner.rs)

## Question
Can an unprivileged attacker who submits deposits and ordinary transactions that compete for the same block space, controlling deposit versus regular transaction mix, drive `extract_receipts_from_working_set` in `crates/sequencer/src/runner.rs` so that the state the block builder reads and the state the block finally commits stop being the same, breaking the invariant that block building reads the state it commits to?

## Target
- File/function: `crates/sequencer/src/runner.rs` -> `extract_receipts_from_working_set`
- Entrypoint: unprivileged party submits deposits and ordinary transactions that compete for the same block space
- Attacker controls: deposit versus regular transaction mix
- Exploit idea: db provider view during build - reach `extract_receipts_from_working_set` from that entrypoint and force the divergence where the state the block builder reads and the state the block finally commits stop being the same; the adjacent symbols in the same file that carry the value are `CitreaSequencer`, `dry_run_transactions`, `save_short_header_proofs`, `produce_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block building reads the state it commits to
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: build under concurrent state changes and diff
