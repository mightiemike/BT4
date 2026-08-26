# Q5306: fee_distribution::calculate_reward_and_burn_fee_details - rounding in the burn split repeatedly favours the attacker (submitting many transactions whose fees each)

## Question
Can an unprivileged attacker who submits fee-paying transactions whose fees are split between burn and the leader's collector, submitting many transactions whose fees each round at the split boundary, drive `fee_distribution::calculate_reward_and_burn_fee_details` to choose fee amounts whose rounding consistently rounds in the collector's favour, so that the invariant that rounding never creates lamports across a block is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/bank/fee_distribution.rs` -> `calculate_reward_and_burn_fee_details`
- Entrypoint: submits fee-paying transactions whose fees are split between burn and the leader's collector, submitting many transactions whose fees each round at the split boundary
- Attacker controls: fee amounts through compute budget and signature counts, and the vote/stake accounts it owns
- Exploit idea: Choose fee amounts whose rounding consistently rounds in the collector's favour.
- Invariant to test: Rounding never creates lamports across a block.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank test running the crafted block and asserting burned plus deposited equals collected fees exactly
