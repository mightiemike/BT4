# Q1560: db provider view during build via `find_block_by_hash` (mod.rs)

## Question
Can an unprivileged attacker who submits a burst of transactions while the sequencer is sealing a block, controlling submission timing, drive `find_block_by_hash` in `crates/sequencer/src/db_provider/mod.rs` so that the state the block builder reads and the state the block finally commits stop being the same, breaking the invariant that block building reads the state it commits to?

## Target
- File/function: `crates/sequencer/src/db_provider/mod.rs` -> `find_block_by_hash`
- Entrypoint: unprivileged party submits a burst of transactions while the sequencer is sealing a block
- Attacker controls: submission timing
- Exploit idea: db provider view during build - reach `find_block_by_hash` from that entrypoint and force the divergence where the state the block builder reads and the state the block finally commits stop being the same; the adjacent symbols in the same file that carry the value are `DbProvider`, `cfg`, `last_block_tx_hashes`, `last_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block building reads the state it commits to
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: build under concurrent state changes and diff
