# Q5174: sealing under an L1 fee-rate change via `save_short_header_proofs` (runner.rs)

## Question
Can an unprivileged attacker who submits deposits and ordinary transactions that compete for the same block space, controlling submission timing, drive `save_short_header_proofs` in `crates/sequencer/src/runner.rs` so that the fee rate the block header records and the rate its transactions were charged at stop being equal, breaking the invariant that a block records the rate it charged?

## Target
- File/function: `crates/sequencer/src/runner.rs` -> `save_short_header_proofs`
- Entrypoint: unprivileged party submits deposits and ordinary transactions that compete for the same block space
- Attacker controls: submission timing
- Exploit idea: sealing under an L1 fee-rate change - reach `save_short_header_proofs` from that entrypoint and force the divergence where the fee rate the block header records and the rate its transactions were charged at stop being equal; the adjacent symbols in the same file that carry the value are `CitreaSequencer`, `dry_run_transactions`, `produce_l2_block`, `produce_l2_block_inner`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a block records the rate it charged
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: change the rate mid-seal and re-execute the block
