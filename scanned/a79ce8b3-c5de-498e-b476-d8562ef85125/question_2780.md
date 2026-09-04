# Q2780: get_last_snapshot_with_sortition_tx: leader key consumed by two commits

## Question
Can an unprivileged attacker reach `get_last_snapshot_with_sortition_tx` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `leader_key_register.rs` lets one key back two commits, breaking the invariant that each leader key == consumed by one commit — leading to sortition manipulation?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `get_last_snapshot_with_sortition_tx`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `leader_key_register.rs` lets one key back two commits
- Invariant to test: each leader key == consumed by one commit
- Expected Immunefi impact: High - sortition manipulation
- Fast validation: test a double-used key
