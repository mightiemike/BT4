# Q5365: stacks_block_index: common_validate_against_burnchain differs by node view

## Question
Can an unprivileged attacker reach `stacks_block_index` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that the burnchain view used differs between nodes, breaking the invariant that the burnchain view a block is validated against == the canonical view — leading to chain split?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `stacks_block_index`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: the burnchain view used differs between nodes
- Invariant to test: the burnchain view a block is validated against == the canonical view
- Expected Immunefi impact: Critical - chain split
- Fast validation: test two burn views
