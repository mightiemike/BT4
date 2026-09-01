# Q2590: db provider view during build via `encode_and_sign_evm_txs_into_sov_txs` (runner.rs)

## Question
Can an unprivileged attacker who submits a burst of transactions while the sequencer is sealing a block, controlling deposit versus regular transaction mix, drive `encode_and_sign_evm_txs_into_sov_txs` in `crates/sequencer/src/runner.rs` so that the state the block builder reads and the state the block finally commits stop being the same, breaking the invariant that block building reads the state it commits to?

## Target
- File/function: `crates/sequencer/src/runner.rs` -> `encode_and_sign_evm_txs_into_sov_txs`
- Entrypoint: unprivileged party submits a burst of transactions while the sequencer is sealing a block
- Attacker controls: deposit versus regular transaction mix
- Exploit idea: db provider view during build - reach `encode_and_sign_evm_txs_into_sov_txs` from that entrypoint and force the divergence where the state the block builder reads and the state the block finally commits stop being the same; the adjacent symbols in the same file that carry the value are `CitreaSequencer`, `dry_run_transactions`, `save_short_header_proofs`, `produce_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block building reads the state it commits to
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: build under concurrent state changes and diff
