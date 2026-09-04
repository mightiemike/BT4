# Q4575: migrate_if_exists: reorg un-matures a paid reward without reversing payment

## Question
Can an unprivileged attacker reach `migrate_if_exists` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that a reorg drops a tenure but keeps its reward, breaking the invariant that rewards paid on the canonical chain == coinbase+fees of canonical tenures — leading to reward loss/inflation?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `migrate_if_exists`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: a reorg drops a tenure but keeps its reward
- Invariant to test: rewards paid on the canonical chain == coinbase+fees of canonical tenures
- Expected Immunefi impact: Critical - reward loss/inflation
- Fast validation: test a reorg after maturation
