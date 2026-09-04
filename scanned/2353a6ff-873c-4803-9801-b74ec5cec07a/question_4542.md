# Q4542: merge_block_header_cache: parent/child reward consolidation adds a reward twice

## Question
Can an unprivileged attacker reach `merge_block_header_cache` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `try_add_parent` consolidates a reward twice, breaking the invariant that each scheduled reward matured == once — leading to reward inflation?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `merge_block_header_cache`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `try_add_parent` consolidates a reward twice
- Invariant to test: each scheduled reward matured == once
- Expected Immunefi impact: Critical - reward inflation
- Fast validation: test a parent/child consolidation
