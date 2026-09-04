# Q1025: find_parent_snapshot_for_stacks_block: Merkle proof verifies for an uncommitted (key,value)

## Question
Can an unprivileged attacker reach `find_parent_snapshot_for_stacks_block` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `proofs.rs` shunt/segment path validates a value never written, breaking the invariant that every verifiable proof == a committed (key,value) — leading to light-client theft via forged proof?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `find_parent_snapshot_for_stacks_block`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `proofs.rs` shunt/segment path validates a value never written
- Invariant to test: every verifiable proof == a committed (key,value)
- Expected Immunefi impact: Critical - light-client theft via forged proof
- Fast validation: test a crafted proof
