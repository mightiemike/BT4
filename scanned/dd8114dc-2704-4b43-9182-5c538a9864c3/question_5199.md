# Q5199: sealing under an L1 fee-rate change via `encode_and_sign_evm_txs_into_sov_txs` (runner.rs)

## Question
Can an unprivileged attacker who submits a burst of transactions while the sequencer is sealing a block, controlling the number and size of transactions per block, drive `encode_and_sign_evm_txs_into_sov_txs` in `crates/sequencer/src/runner.rs` so that the fee rate the block header records and the rate its transactions were charged at stop being equal, breaking the invariant that a block records the rate it charged?

## Target
- File/function: `crates/sequencer/src/runner.rs` -> `encode_and_sign_evm_txs_into_sov_txs`
- Entrypoint: unprivileged party submits a burst of transactions while the sequencer is sealing a block
- Attacker controls: the number and size of transactions per block
- Exploit idea: sealing under an L1 fee-rate change - reach `encode_and_sign_evm_txs_into_sov_txs` from that entrypoint and force the divergence where the fee rate the block header records and the rate its transactions were charged at stop being equal; the adjacent symbols in the same file that carry the value are `CitreaSequencer`, `dry_run_transactions`, `save_short_header_proofs`, `produce_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a block records the rate it charged
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: change the rate mid-seal and re-execute the block
