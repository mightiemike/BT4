# Q1073: find_snapshots_with_dirty_canonical_block_pointers: burn distribution sum overflows

## Question
Can an unprivileged attacker reach `find_snapshots_with_dirty_canonical_block_pointers` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `distribution.rs` overflows the burn-fee sum, breaking the invariant that the weight computed == the true summed burn — leading to sortition manipulation?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `find_snapshots_with_dirty_canonical_block_pointers`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `distribution.rs` overflows the burn-fee sum
- Invariant to test: the weight computed == the true summed burn
- Expected Immunefi impact: Critical - sortition manipulation
- Fast validation: test an overflowing burn sum
