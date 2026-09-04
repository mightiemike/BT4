# Q3826: index_handle_at_ch: shadow block promoted into the canonical chain

## Question
Can an unprivileged attacker reach `index_handle_at_ch` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `shadow.rs` lets a shadow block become canonical, breaking the invariant that canonical blocks == non-shadow validated blocks — leading to fork / invalid state?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `index_handle_at_ch`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `shadow.rs` lets a shadow block become canonical
- Invariant to test: canonical blocks == non-shadow validated blocks
- Expected Immunefi impact: Critical - fork / invalid state
- Fast validation: test a shadow promotion
