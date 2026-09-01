# Q3724: deposit versus user tx block budget via `extract_bundle_state_from_state_log` (runner.rs)

## Question
Can an unprivileged attacker who submits deposits and ordinary transactions that compete for the same block space, controlling the number and size of transactions per block, drive `extract_bundle_state_from_state_log` in `crates/sequencer/src/runner.rs` so that the block the sequencer builds and the block the STF re-executes stop being the same block, breaking the invariant that block construction is replay-deterministic?

## Target
- File/function: `crates/sequencer/src/runner.rs` -> `extract_bundle_state_from_state_log`
- Entrypoint: unprivileged party submits deposits and ordinary transactions that compete for the same block space
- Attacker controls: the number and size of transactions per block
- Exploit idea: deposit versus user tx block budget - reach `extract_bundle_state_from_state_log` from that entrypoint and force the divergence where the block the sequencer builds and the block the STF re-executes stop being the same block; the adjacent symbols in the same file that carry the value are `CitreaSequencer`, `dry_run_transactions`, `save_short_header_proofs`, `produce_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block construction is replay-deterministic
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: rebuild the block from stored data and diff the root
