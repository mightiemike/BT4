# Q2428: get_highest_block_height_from_path: get_coinbase_height mis-maps across a reorg

## Question
Can an unprivileged attacker reach `get_highest_block_height_from_path` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `get_coinbase_height`/`get_header_by_coinbase_height` returns a sibling-fork block, breaking the invariant that coinbase height == the canonical height mapping — leading to reward/tenure mis-accounting?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `get_highest_block_height_from_path`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `get_coinbase_height`/`get_header_by_coinbase_height` returns a sibling-fork block
- Invariant to test: coinbase height == the canonical height mapping
- Expected Immunefi impact: High - reward/tenure mis-accounting
- Fast validation: test a reorg height query
