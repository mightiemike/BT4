# Q4294: last_reward_cycle_to_string: state root committed differs from recomputed root

## Question
Can an unprivileged attacker reach `last_reward_cycle_to_string` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that MARF hashing depends on serialization order or a stale back-pointer, breaking the invariant that committed root == the root every node's MARF produces — leading to consensus failure / fork?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `last_reward_cycle_to_string`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: MARF hashing depends on serialization order or a stale back-pointer
- Invariant to test: committed root == the root every node's MARF produces
- Expected Immunefi impact: Critical - consensus failure / fork
- Fast validation: two-node test asserting equal roots
