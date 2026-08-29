# Q0800: mask-pos via collateral-remove-redeem: leave the accrual clock stale so a later interval double-c

## Question
Does `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211) let an unprivileged attacker who controls remaining zToken collateral whose price moves with the redeem reach `mask-pos` (mainnet/contracts/market/v0-market-vault.clar:91) in a state where it leave the accrual clock stale so a later interval double-counts elapsed time? Given that it maps an asset id to a bit position, offsetting debt bits by DEBT-OFFSET, the invariant that the state a safety check approved is the state the money movement executes against breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:91` -> `mask-pos`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: remaining zToken collateral whose price moves with the redeem
- Exploit idea: `mask-pos` maps an asset id to a bit position, offsetting debt bits by DEBT-OFFSET. Reach it through `collateral-remove-redeem` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `collateral-remove-redeem` twice with remaining zToken collateral whose price moves with the redeem varied, and assert that the value `mask-pos` returns is identical in both runs; a divergence confirms the finding.
