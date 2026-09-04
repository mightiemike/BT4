# Q0240: is_overflowed: record_block_signers double-counts a signer

## Question
Can an unprivileged attacker reach `is_overflowed` (in `stackslib/src/chainstate/burn/atc.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that a signer signature is recorded twice toward weight, breaking the invariant that recorded weight == distinct verified signer weight — leading to weight inflation?

## Target
- File/function: `stackslib/src/chainstate/burn/atc.rs` -> `is_overflowed`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: a signer signature is recorded twice toward weight
- Invariant to test: recorded weight == distinct verified signer weight
- Expected Immunefi impact: Critical - weight inflation
- Fast validation: test a duplicate signer record
