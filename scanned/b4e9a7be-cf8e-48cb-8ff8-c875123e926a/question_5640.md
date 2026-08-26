# Q5640: inflation_points::calculate_points_for_tower - points computed from credits the vote account did not earn

## Question
Can an unprivileged attacker who delegates stake to a vote account it controls and accumulates epoch credits, delegating in the last slot before the epoch boundary, drive `inflation_points::calculate_points_for_tower` to make calc_earned_credits or tower_epoch_credits_iter count credits outside the reward epoch, so that the invariant that points count only credits earned in the epoch being rewarded is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/inflation_rewards/points.rs` -> `calculate_points_for_tower`
- Entrypoint: delegates stake to a vote account it controls and accumulates epoch credits, delegating in the last slot before the epoch boundary
- Attacker controls: stake amounts, delegation activation timing, vote credit history and commission settings
- Exploit idea: Make calc_earned_credits or tower_epoch_credits_iter count credits outside the reward epoch.
- Invariant to test: Points count only credits earned in the epoch being rewarded.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the point calculation against the crafted stake and credit history and assert points match the protocol formula
