# Q3152: sealing under an L1 fee-rate change via `ommers_by_number_or_tag` (mod.rs)

## Question
Can an unprivileged attacker who submits deposits and ordinary transactions that compete for the same block space, controlling submission timing, drive `ommers_by_number_or_tag` in `crates/sequencer/src/db_provider/mod.rs` so that the fee rate the block header records and the rate its transactions were charged at stop being equal, breaking the invariant that a block records the rate it charged?

## Target
- File/function: `crates/sequencer/src/db_provider/mod.rs` -> `ommers_by_number_or_tag`
- Entrypoint: unprivileged party submits deposits and ordinary transactions that compete for the same block space
- Attacker controls: submission timing
- Exploit idea: sealing under an L1 fee-rate change - reach `ommers_by_number_or_tag` from that entrypoint and force the divergence where the fee rate the block header records and the rate its transactions were charged at stop being equal; the adjacent symbols in the same file that carry the value are `DbProvider`, `cfg`, `last_block_tx_hashes`, `last_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a block records the rate it charged
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: change the rate mid-seal and re-execute the block
