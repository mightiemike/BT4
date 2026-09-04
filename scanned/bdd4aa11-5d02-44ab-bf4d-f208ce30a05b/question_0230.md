# Q0230: is_overflowed: leader key consumed by two commits

## Question
Can an unprivileged attacker reach `is_overflowed` (in `stackslib/src/chainstate/burn/atc.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `leader_key_register.rs` lets one key back two commits, breaking the invariant that each leader key == consumed by one commit — leading to sortition manipulation?

## Target
- File/function: `stackslib/src/chainstate/burn/atc.rs` -> `is_overflowed`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `leader_key_register.rs` lets one key back two commits
- Invariant to test: each leader key == consumed by one commit
- Expected Immunefi impact: High - sortition manipulation
- Fast validation: test a double-used key
