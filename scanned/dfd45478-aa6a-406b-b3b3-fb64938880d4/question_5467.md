# Q5467: epoch_rewards_calculation::calculate_stake_rewards_and_commissions - delegation redemption double counts credits

## Question
Can an unprivileged attacker who creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, delegating stake in the final slot before the epoch boundary, drive `epoch_rewards_calculation::calculate_stake_rewards_and_commissions` to make redeem_delegation_rewards consume the same epoch credits twice, so that the invariant that each epoch credit is redeemed at most once per stake account is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` -> `calculate_stake_rewards_and_commissions`
- Entrypoint: creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, delegating stake in the final slot before the epoch boundary
- Attacker controls: stake amounts, delegation and deactivation timing, vote account commission, and credit history
- Exploit idea: Make redeem_delegation_rewards consume the same epoch credits twice.
- Invariant to test: Each epoch credit is redeemed at most once per stake account.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank test warping across the epoch boundary with the crafted stake set and asserting rewards equal the point-based expectation
