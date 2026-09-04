# Q4572: migrate_if_exists: parent/child reward consolidation adds a reward twice

## Question
Can an unprivileged attacker reach `migrate_if_exists` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `try_add_parent` consolidates a reward twice, breaking the invariant that each scheduled reward matured == once — leading to reward inflation?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `migrate_if_exists`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `try_add_parent` consolidates a reward twice
- Invariant to test: each scheduled reward matured == once
- Expected Immunefi impact: Critical - reward inflation
- Fast validation: test a parent/child consolidation
