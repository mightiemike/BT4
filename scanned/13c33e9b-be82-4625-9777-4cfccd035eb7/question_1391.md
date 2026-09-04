# Q1391: get_block_burn_amount: streamed fee split over-credits

## Question
Can an unprivileged attacker reach `get_block_burn_amount` (in `stackslib/src/chainstate/burn/db/sortdb.rs`) via a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only), such that `streamed_tx_fees_confirmed` vs `_produced` over-credit fees, breaking the invariant that fees credited == fees actually confirmed for the tenure — leading to fee over-payment?

## Target
- File/function: `stackslib/src/chainstate/burn/db/sortdb.rs` -> `get_block_burn_amount`
- Entrypoint: a Bitcoin block-commit/leader-key the attacker broadcasts, a Nakamoto block/microblock they submit, a poison report, or a fork they extend (minority resources only)
- Attacker controls: their own block-commit and leader-key fields, submitted block contents and signatures, poison-report headers, and the fork they build
- Exploit idea: `streamed_tx_fees_confirmed` vs `_produced` over-credit fees
- Invariant to test: fees credited == fees actually confirmed for the tenure
- Expected Immunefi impact: High - fee over-payment
- Fast validation: test the fee split
