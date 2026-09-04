# Q3467: get_stacks_epoch_by_epoch_id: poison-microblock slashes a non-equivocating miner

## Question
Can an unprivileged attacker reach `get_stacks_epoch_by_epoch_id` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `handle_poison_microblock` accepts two headers that are not a valid double-sign, breaking the invariant that a slash == a valid, unreported double-signature under the miner's key — leading to unjust slash / theft?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `get_stacks_epoch_by_epoch_id`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `handle_poison_microblock` accepts two headers that are not a valid double-sign
- Invariant to test: a slash == a valid, unreported double-signature under the miner's key
- Expected Immunefi impact: Critical - unjust slash / theft
- Fast validation: test two non-conflicting headers
