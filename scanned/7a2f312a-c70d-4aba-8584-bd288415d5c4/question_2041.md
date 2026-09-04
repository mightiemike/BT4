# Q2041: get_canonical_stacks_chain_tip_hash: sortition winner not the burn-weight/VRF function's output

## Question
Can an unprivileged attacker reach `get_canonical_stacks_chain_tip_hash` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `sortition.rs`/`distribution.rs` selection depends on map/float ordering or a tie-break, breaking the invariant that the accepted winner == the deterministic burn-weight+VRF winner on every node — leading to chain split / stolen tenure?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `get_canonical_stacks_chain_tip_hash`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `sortition.rs`/`distribution.rs` selection depends on map/float ordering or a tie-break
- Invariant to test: the accepted winner == the deterministic burn-weight+VRF winner on every node
- Expected Immunefi impact: Critical - chain split / stolen tenure
- Fast validation: two-node test asserting identical winner
