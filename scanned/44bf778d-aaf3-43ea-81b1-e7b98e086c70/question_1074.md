# Q1074: find_snapshots_with_dirty_canonical_block_pointers: ATC adjustment applied inconsistently across nodes

## Question
Can an unprivileged attacker reach `find_snapshots_with_dirty_canonical_block_pointers` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `atc.rs` target adjustment diverges, breaking the invariant that the ATC target on node A == on node B — leading to sortition divergence?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `find_snapshots_with_dirty_canonical_block_pointers`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `atc.rs` target adjustment diverges
- Invariant to test: the ATC target on node A == on node B
- Expected Immunefi impact: Critical - sortition divergence
- Fast validation: test the ATC path on two nodes
