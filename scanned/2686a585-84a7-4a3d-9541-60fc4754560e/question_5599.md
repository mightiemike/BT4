# Q5599: test_insert_block_commit_parent_row: VRF proof accepted not matching the committed seed

## Question
Can an unprivileged attacker reach `test_insert_block_commit_parent_row` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `validate_vrf_seed`/`check_block_commit_vrf_seed` binds the wrong seed/key, breaking the invariant that the VRF proof == a proof under the committed seed and leader key — leading to randomness manipulation?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `test_insert_block_commit_parent_row`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `validate_vrf_seed`/`check_block_commit_vrf_seed` binds the wrong seed/key
- Invariant to test: the VRF proof == a proof under the committed seed and leader key
- Expected Immunefi impact: High - randomness manipulation
- Fast validation: test a mismatched VRF proof
