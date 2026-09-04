# Q2469: get_initial_mining_bonus_per_block: coinbase reward paid twice across a fork

## Question
Can an unprivileged attacker reach `get_initial_mining_bonus_per_block` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that maturation double-counts on two branches, breaking the invariant that STX paid as reward for a tenure == coinbase+fees, once — leading to reward double-payment?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `get_initial_mining_bonus_per_block`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: maturation double-counts on two branches
- Invariant to test: STX paid as reward for a tenure == coinbase+fees, once
- Expected Immunefi impact: Critical - reward double-payment
- Fast validation: test a fork maturation
