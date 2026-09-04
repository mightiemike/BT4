# Q4227: is_stacks_block_pox_anchor: trie path collision from ambiguous key encoding

## Question
Can an unprivileged attacker reach `is_stacks_block_pox_anchor` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that two distinct keys share a trie path, breaking the invariant that each committed key == a unique trie path — leading to state corruption?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `is_stacks_block_pox_anchor`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: two distinct keys share a trie path
- Invariant to test: each committed key == a unique trie path
- Expected Immunefi impact: Critical - state corruption
- Fast validation: test colliding keys
