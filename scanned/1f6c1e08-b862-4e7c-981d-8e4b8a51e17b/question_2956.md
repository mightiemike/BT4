# Q2956: deposit versus user tx block budget via `last_block_tx_hashes` (mod.rs)

## Question
Can an unprivileged attacker who submits deposits and ordinary transactions that compete for the same block space, controlling submission timing, drive `last_block_tx_hashes` in `crates/sequencer/src/db_provider/mod.rs` so that the block the sequencer builds and the block the STF re-executes stop being the same block, breaking the invariant that block construction is replay-deterministic?

## Target
- File/function: `crates/sequencer/src/db_provider/mod.rs` -> `last_block_tx_hashes`
- Entrypoint: unprivileged party submits deposits and ordinary transactions that compete for the same block space
- Attacker controls: submission timing
- Exploit idea: deposit versus user tx block budget - reach `last_block_tx_hashes` from that entrypoint and force the divergence where the block the sequencer builds and the block the STF re-executes stop being the same block; the adjacent symbols in the same file that carry the value are `DbProvider`, `cfg`, `last_block`, `genesis_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block construction is replay-deterministic
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: rebuild the block from stored data and diff the root
