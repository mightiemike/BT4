# Q2776: l1 fee rate selection via `get_best_transactions` (runner.rs)

## Question
Can an unprivileged attacker who submits deposits and ordinary transactions that compete for the same block space, controlling the number and size of transactions per block, drive `get_best_transactions` in `crates/sequencer/src/runner.rs` so that the L1 fee rate used to charge transactions and the rate recorded in the block stop being equal, breaking the invariant that the charged rate is the recorded rate?

## Target
- File/function: `crates/sequencer/src/runner.rs` -> `get_best_transactions`
- Entrypoint: unprivileged party submits deposits and ordinary transactions that compete for the same block space
- Attacker controls: the number and size of transactions per block
- Exploit idea: l1 fee rate selection - reach `get_best_transactions` from that entrypoint and force the divergence where the L1 fee rate used to charge transactions and the rate recorded in the block stop being equal; the adjacent symbols in the same file that carry the value are `CitreaSequencer`, `dry_run_transactions`, `save_short_header_proofs`, `produce_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the charged rate is the recorded rate
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: cross a fee-rate update mid-block and compare
