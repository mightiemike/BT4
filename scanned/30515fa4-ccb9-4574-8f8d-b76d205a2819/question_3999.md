# Q3999: l1 fee rate selection via `header_td_by_number` (mod.rs)

## Question
Can an unprivileged attacker who submits a burst of transactions while the sequencer is sealing a block, controlling the number and size of transactions per block, drive `header_td_by_number` in `crates/sequencer/src/db_provider/mod.rs` so that the L1 fee rate used to charge transactions and the rate recorded in the block stop being equal, breaking the invariant that the charged rate is the recorded rate?

## Target
- File/function: `crates/sequencer/src/db_provider/mod.rs` -> `header_td_by_number`
- Entrypoint: unprivileged party submits a burst of transactions while the sequencer is sealing a block
- Attacker controls: the number and size of transactions per block
- Exploit idea: l1 fee rate selection - reach `header_td_by_number` from that entrypoint and force the divergence where the L1 fee rate used to charge transactions and the rate recorded in the block stop being equal; the adjacent symbols in the same file that carry the value are `DbProvider`, `cfg`, `last_block_tx_hashes`, `last_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the charged rate is the recorded rate
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: cross a fee-rate update mid-block and compare
