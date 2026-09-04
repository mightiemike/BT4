# Q2088: get_canonical_stacks_chain_tip_hash_and_height: poison reward collected twice for one offense

## Question
Can an unprivileged attacker reach `get_canonical_stacks_chain_tip_hash_and_height` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that the report can be replayed for one double-sign, breaking the invariant that a poison reward == exactly one unreported offense — leading to reward double-collection?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `get_canonical_stacks_chain_tip_hash_and_height`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: the report can be replayed for one double-sign
- Invariant to test: a poison reward == exactly one unreported offense
- Expected Immunefi impact: High - reward double-collection
- Fast validation: test a replayed report
