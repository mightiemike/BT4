# Q5243: fee_distribution::calculate_reward_and_burn_fee_details - burn plus deposit does not equal collected fees

## Question
Can an unprivileged attacker who submits fee-paying transactions whose fees are split between burn and the leader's collector, paying the maximum possible prioritization fee in one transaction, drive `fee_distribution::calculate_reward_and_burn_fee_details` to make calculate_reward_and_burn_fee_details split fees so lamports are created or destroyed, so that the invariant that burned plus deposited lamports equal the fees collected is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/bank/fee_distribution.rs` -> `calculate_reward_and_burn_fee_details`
- Entrypoint: submits fee-paying transactions whose fees are split between burn and the leader's collector, paying the maximum possible prioritization fee in one transaction
- Attacker controls: fee amounts through compute budget and signature counts, and the vote/stake accounts it owns
- Exploit idea: Make calculate_reward_and_burn_fee_details split fees so lamports are created or destroyed.
- Invariant to test: Burned plus deposited lamports equal the fees collected.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank test running the crafted block and asserting burned plus deposited equals collected fees exactly
