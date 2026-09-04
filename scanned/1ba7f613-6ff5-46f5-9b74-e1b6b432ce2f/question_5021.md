# Q5021: pox_reward_set_size: streamed fee split over-credits

## Question
Can an unprivileged attacker reach `pox_reward_set_size` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `streamed_tx_fees_confirmed` vs `_produced` over-credit fees, breaking the invariant that fees credited == fees actually confirmed for the tenure — leading to fee over-payment?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `pox_reward_set_size`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `streamed_tx_fees_confirmed` vs `_produced` over-credit fees
- Invariant to test: fees credited == fees actually confirmed for the tenure
- Expected Immunefi impact: High - fee over-payment
- Fast validation: test the fee split
