# Q1143: get_all_snapshots_by_burn_height: signer_signature_hash omits a field the node acts on

## Question
Can an unprivileged attacker reach `get_all_snapshots_by_burn_height` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that a signature over one block validates another, breaking the invariant that the block a signature authenticates == the block the node executes — leading to signature reuse across blocks?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `get_all_snapshots_by_burn_height`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: a signature over one block validates another
- Invariant to test: the block a signature authenticates == the block the node executes
- Expected Immunefi impact: Critical - signature reuse across blocks
- Fast validation: test a field outside the hash
