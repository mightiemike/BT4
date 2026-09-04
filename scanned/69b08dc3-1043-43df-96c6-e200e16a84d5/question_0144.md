# Q0144: into_inner: ATC adjustment applied inconsistently across nodes

## Question
Can an unprivileged attacker reach `into_inner` (in `stackslib/src/chainstate/burn/atc.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `atc.rs` target adjustment diverges, breaking the invariant that the ATC target on node A == on node B — leading to sortition divergence?

## Target
- File/function: `stackslib/src/chainstate/burn/atc.rs` -> `into_inner`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `atc.rs` target adjustment diverges
- Invariant to test: the ATC target on node A == on node B
- Expected Immunefi impact: Critical - sortition divergence
- Fast validation: test the ATC path on two nodes
