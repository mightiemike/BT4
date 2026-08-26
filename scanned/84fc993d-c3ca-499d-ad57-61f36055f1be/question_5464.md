# Q5464: epoch_rewards_calculation::calculate_validator_rewards - total rewards exceed the epoch inflation budget

## Question
Can an unprivileged attacker who creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, delegating stake in the final slot before the epoch boundary, drive `epoch_rewards_calculation::calculate_validator_rewards` to make the sum of distributed rewards exceed calculate_block_reward's budget for the epoch, so that the invariant that total rewards never exceed the inflation budget for the epoch is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` -> `calculate_validator_rewards`
- Entrypoint: creates and delegates its own stake accounts to a vote account it controls, then waits for the epoch boundary, delegating stake in the final slot before the epoch boundary
- Attacker controls: stake amounts, delegation and deactivation timing, vote account commission, and credit history
- Exploit idea: Make the sum of distributed rewards exceed calculate_block_reward's budget for the epoch.
- Invariant to test: Total rewards never exceed the inflation budget for the epoch.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank test warping across the epoch boundary with the crafted stake set and asserting rewards equal the point-based expectation
