# Q1993: get_canonical_nakamoto_tip_hash_and_height_and_burn_view: two nodes select different canonical tips

## Question
Can an unprivileged attacker reach `get_canonical_nakamoto_tip_hash_and_height_and_burn_view` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that staging/coordinator fork choice depends on arrival order, breaking the invariant that the canonical tip each node picks == the same block — leading to chain split?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `get_canonical_nakamoto_tip_hash_and_height_and_burn_view`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: staging/coordinator fork choice depends on arrival order
- Invariant to test: the canonical tip each node picks == the same block
- Expected Immunefi impact: Critical - chain split
- Fast validation: two-node test asserting tip
