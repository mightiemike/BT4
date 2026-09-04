# Q4923: pox_reward_set_payouts_decode: signer_signature_hash omits a field the node acts on

## Question
Can an unprivileged attacker reach `pox_reward_set_payouts_decode` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that a signature over one block validates another, breaking the invariant that the block a signature authenticates == the block the node executes — leading to signature reuse across blocks?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `pox_reward_set_payouts_decode`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: a signature over one block validates another
- Invariant to test: the block a signature authenticates == the block the node executes
- Expected Immunefi impact: Critical - signature reuse across blocks
- Fast validation: test a field outside the hash
