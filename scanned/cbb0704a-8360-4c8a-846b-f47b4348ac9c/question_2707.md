# Q2707: deposit versus user tx block budget via `build_reth_block_data` (runner.rs)

## Question
Can an unprivileged attacker who submits a burst of transactions while the sequencer is sealing a block, controlling submission timing, drive `build_reth_block_data` in `crates/sequencer/src/runner.rs` so that the block the sequencer builds and the block the STF re-executes stop being the same block, breaking the invariant that block construction is replay-deterministic?

## Target
- File/function: `crates/sequencer/src/runner.rs` -> `build_reth_block_data`
- Entrypoint: unprivileged party submits a burst of transactions while the sequencer is sealing a block
- Attacker controls: submission timing
- Exploit idea: deposit versus user tx block budget - reach `build_reth_block_data` from that entrypoint and force the divergence where the block the sequencer builds and the block the STF re-executes stop being the same block; the adjacent symbols in the same file that carry the value are `CitreaSequencer`, `dry_run_transactions`, `save_short_header_proofs`, `produce_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block construction is replay-deterministic
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: rebuild the block from stored data and diff the root
