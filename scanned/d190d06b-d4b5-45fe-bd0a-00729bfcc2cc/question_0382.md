# Q0382: one_sup: block-commit with a non-existent leader key accepted

## Question
Can an unprivileged attacker reach `one_sup` (in `stackslib/src/chainstate/burn/atc.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `leader_block_commit.rs` `check` misses a dangling key ref, breaking the invariant that every accepted commit == referencing a registered key — leading to invalid commit in sortition?

## Target
- File/function: `stackslib/src/chainstate/burn/atc.rs` -> `one_sup`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `leader_block_commit.rs` `check` misses a dangling key ref
- Invariant to test: every accepted commit == referencing a registered key
- Expected Immunefi impact: High - invalid commit in sortition
- Fast validation: test a dangling-key commit
