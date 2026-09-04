# Q4919: pox_reward_set_entry: pox bitvector admits a signer not in the set

## Question
Can an unprivileged attacker reach `pox_reward_set_entry` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `check_pox_bitvector` mismatch lets an outsider count, breaking the invariant that signers counted == exactly the reward-set members — leading to unsigned finalisation?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `pox_reward_set_entry`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `check_pox_bitvector` mismatch lets an outsider count
- Invariant to test: signers counted == exactly the reward-set members
- Expected Immunefi impact: Critical - unsigned finalisation
- Fast validation: test a bitvector mismatch
