# Q2100: get_canonical_stacks_chain_tip_hash_and_height: record_block_signers double-counts a signer

## Question
Can an unprivileged attacker reach `get_canonical_stacks_chain_tip_hash_and_height` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that a signer signature is recorded twice toward weight, breaking the invariant that recorded weight == distinct verified signer weight — leading to weight inflation?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `get_canonical_stacks_chain_tip_hash_and_height`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: a signer signature is recorded twice toward weight
- Invariant to test: recorded weight == distinct verified signer weight
- Expected Immunefi impact: Critical - weight inflation
- Fast validation: test a duplicate signer record
