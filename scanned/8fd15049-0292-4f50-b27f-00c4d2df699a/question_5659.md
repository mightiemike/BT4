# Q5659: inflation_points::calculate_stake_points_for_tower - point arithmetic overflows into a large value (accumulating credits on a vote account)

## Question
Can an unprivileged attacker who delegates stake to a vote account it controls and accumulates epoch credits, accumulating credits on a vote account that changes commission mid-epoch, drive `inflation_points::calculate_stake_points_for_tower` to choose stake and credit values whose product overflows the point accumulator, so that the invariant that point computation uses saturating or checked wide arithmetic is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/inflation_rewards/points.rs` -> `calculate_stake_points_for_tower`
- Entrypoint: delegates stake to a vote account it controls and accumulates epoch credits, accumulating credits on a vote account that changes commission mid-epoch
- Attacker controls: stake amounts, delegation activation timing, vote credit history and commission settings
- Exploit idea: Choose stake and credit values whose product overflows the point accumulator.
- Invariant to test: Point computation uses saturating or checked wide arithmetic.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the point calculation against the crafted stake and credit history and assert points match the protocol formula
