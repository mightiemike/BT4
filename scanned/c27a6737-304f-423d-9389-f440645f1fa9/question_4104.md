# Q4104: sealing under an L1 fee-rate change via `find_block_by_hash` (mod.rs)

## Question
Can an unprivileged attacker who submits a burst of transactions while the sequencer is sealing a block, controlling the number and size of transactions per block, drive `find_block_by_hash` in `crates/sequencer/src/db_provider/mod.rs` so that the fee rate the block header records and the rate its transactions were charged at stop being equal, breaking the invariant that a block records the rate it charged?

## Target
- File/function: `crates/sequencer/src/db_provider/mod.rs` -> `find_block_by_hash`
- Entrypoint: unprivileged party submits a burst of transactions while the sequencer is sealing a block
- Attacker controls: the number and size of transactions per block
- Exploit idea: sealing under an L1 fee-rate change - reach `find_block_by_hash` from that entrypoint and force the divergence where the fee rate the block header records and the rate its transactions were charged at stop being equal; the adjacent symbols in the same file that carry the value are `DbProvider`, `cfg`, `last_block_tx_hashes`, `last_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a block records the rate it charged
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: change the rate mid-seal and re-execute the block
