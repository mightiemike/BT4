# Q5650: inflation_points::calculate_stake_points_and_credits - error path silently yields points

## Question
Can an unprivileged attacker who delegates stake to a vote account it controls and accumulates epoch credits, delegating in the last slot before the epoch boundary, drive `inflation_points::calculate_stake_points_and_credits` to trigger record_error and still receive a non-zero point contribution, so that the invariant that an errored point calculation contributes zero points is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/inflation_rewards/points.rs` -> `calculate_stake_points_and_credits`
- Entrypoint: delegates stake to a vote account it controls and accumulates epoch credits, delegating in the last slot before the epoch boundary
- Attacker controls: stake amounts, delegation activation timing, vote credit history and commission settings
- Exploit idea: Trigger record_error and still receive a non-zero point contribution.
- Invariant to test: An errored point calculation contributes zero points.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the point calculation against the crafted stake and credit history and assert points match the protocol formula
