# Q0100: inner: miner reward credited to an unauthenticated recipient

## Question
Can an unprivileged attacker reach `inner` (in `stackslib/src/chainstate/burn/atc.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that the recipient is read from an unauthenticated coinbase field, breaking the invariant that reward recipient == the sortition winner's committed recipient — leading to reward theft?

## Target
- File/function: `stackslib/src/chainstate/burn/atc.rs` -> `inner`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: the recipient is read from an unauthenticated coinbase field
- Invariant to test: reward recipient == the sortition winner's committed recipient
- Expected Immunefi impact: Critical - reward theft
- Fast validation: test a crafted coinbase recipient
