# Q3794: index_handle_at_block: staged block builds on an invalid/unavailable parent

## Question
Can an unprivileged attacker reach `index_handle_at_block` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `staging_blocks.rs` accepts a block on a bad parent, breaking the invariant that every staged block == built on a validated available parent — leading to fork / invalid acceptance?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `index_handle_at_block`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `staging_blocks.rs` accepts a block on a bad parent
- Invariant to test: every staged block == built on a validated available parent
- Expected Immunefi impact: Critical - fork / invalid acceptance
- Fast validation: test a bad-parent block
