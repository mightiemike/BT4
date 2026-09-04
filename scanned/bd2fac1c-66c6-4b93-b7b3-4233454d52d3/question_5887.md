# Q5887: test_insert_stacks_chain_tip_row: tenure extended past its allowed length

## Question
Can an unprivileged attacker reach `test_insert_stacks_chain_tip_row` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `get_nakamoto_tenure_length` lets a tenure over-extend, breaking the invariant that tenure length <= the allowed bound — leading to tenure abuse / liveness?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `test_insert_stacks_chain_tip_row`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `get_nakamoto_tenure_length` lets a tenure over-extend
- Invariant to test: tenure length <= the allowed bound
- Expected Immunefi impact: High - tenure abuse / liveness
- Fast validation: test an over-long tenure
