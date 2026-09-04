# Q0239: is_overflowed: pox bitvector admits a signer not in the set

## Question
Can an unprivileged attacker reach `is_overflowed` (in `stackslib/src/chainstate/burn/atc.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `check_pox_bitvector` mismatch lets an outsider count, breaking the invariant that signers counted == exactly the reward-set members — leading to unsigned finalisation?

## Target
- File/function: `stackslib/src/chainstate/burn/atc.rs` -> `is_overflowed`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `check_pox_bitvector` mismatch lets an outsider count
- Invariant to test: signers counted == exactly the reward-set members
- Expected Immunefi impact: Critical - unsigned finalisation
- Fast validation: test a bitvector mismatch
