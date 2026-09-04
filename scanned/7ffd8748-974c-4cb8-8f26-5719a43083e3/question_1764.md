# Q1764: get_block_snapshot_of_parent_stacks_block: ATC adjustment applied inconsistently across nodes

## Question
Can an unprivileged attacker reach `get_block_snapshot_of_parent_stacks_block` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `atc.rs` target adjustment diverges, breaking the invariant that the ATC target on node A == on node B — leading to sortition divergence?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `get_block_snapshot_of_parent_stacks_block`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `atc.rs` target adjustment diverges
- Invariant to test: the ATC target on node A == on node B
- Expected Immunefi impact: Critical - sortition divergence
- Fast validation: test the ATC path on two nodes
