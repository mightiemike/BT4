# Q5664: inflation_points::calculate_stake_points_and_credits - credits counted twice across epochs (accumulating credits on a vote account)

## Question
Can an unprivileged attacker who delegates stake to a vote account it controls and accumulates epoch credits, accumulating credits on a vote account that changes commission mid-epoch, drive `inflation_points::calculate_stake_points_and_credits` to make the credits iterator include the same epoch twice for one stake account, so that the invariant that each epoch's credits contribute to points exactly once is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/inflation_rewards/points.rs` -> `calculate_stake_points_and_credits`
- Entrypoint: delegates stake to a vote account it controls and accumulates epoch credits, accumulating credits on a vote account that changes commission mid-epoch
- Attacker controls: stake amounts, delegation activation timing, vote credit history and commission settings
- Exploit idea: Make the credits iterator include the same epoch twice for one stake account.
- Invariant to test: Each epoch's credits contribute to points exactly once.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the point calculation against the crafted stake and credit history and assert points match the protocol formula
