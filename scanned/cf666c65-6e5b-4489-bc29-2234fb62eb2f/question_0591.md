# Q0591: append_chain_tip_snapshot: static validation verdict differs across nodes

## Question
Can an unprivileged attacker reach `append_chain_tip_snapshot` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `validate_transactions_static`/`validate_nakamoto_block_static` reads mutable config, breaking the invariant that the static verdict for a block == the same on every node — leading to chain split?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `append_chain_tip_snapshot`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `validate_transactions_static`/`validate_nakamoto_block_static` reads mutable config
- Invariant to test: the static verdict for a block == the same on every node
- Expected Immunefi impact: Critical - chain split
- Fast validation: test the validator under two configs
