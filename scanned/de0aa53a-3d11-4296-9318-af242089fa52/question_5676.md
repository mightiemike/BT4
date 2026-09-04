# Q5676: test_insert_leader_key_row: tenure-change accepted not authorised by its sortition

## Question
Can an unprivileged attacker reach `test_insert_leader_key_row` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `check_tenure_tx`/`validate_nakamoto_tenure_snapshot` binds the wrong sortition, breaking the invariant that each block's tenure == the tenure its winning sortition authorised — leading to tenure hijack / fork?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `test_insert_leader_key_row`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `check_tenure_tx`/`validate_nakamoto_tenure_snapshot` binds the wrong sortition
- Invariant to test: each block's tenure == the tenure its winning sortition authorised
- Expected Immunefi impact: Critical - tenure hijack / fork
- Fast validation: test a mis-authorised tenure-change
