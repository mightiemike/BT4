# Q1778: get_block_winning_vtxindex: consensus hash reused across two tenures

## Question
Can an unprivileged attacker reach `get_block_winning_vtxindex` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that a replayed tenure-change reuses a consensus hash, breaking the invariant that each consensus hash == exactly one tenure — leading to fork via duplicate tenure?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `get_block_winning_vtxindex`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: a replayed tenure-change reuses a consensus hash
- Invariant to test: each consensus hash == exactly one tenure
- Expected Immunefi impact: Critical - fork via duplicate tenure
- Fast validation: test a replayed tenure id
