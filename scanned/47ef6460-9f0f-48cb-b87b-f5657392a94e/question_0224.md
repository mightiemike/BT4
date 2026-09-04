# Q0224: is_overflowed: staged block builds on an invalid/unavailable parent

## Question
Can an unprivileged attacker reach `is_overflowed` (in `stackslib/src/chainstate/burn/atc.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `staging_blocks.rs` accepts a block on a bad parent, breaking the invariant that every staged block == built on a validated available parent — leading to fork / invalid acceptance?

## Target
- File/function: `stackslib/src/chainstate/burn/atc.rs` -> `is_overflowed`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `staging_blocks.rs` accepts a block on a bad parent
- Invariant to test: every staged block == built on a validated available parent
- Expected Immunefi impact: Critical - fork / invalid acceptance
- Fast validation: test a bad-parent block
