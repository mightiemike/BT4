# Q3259: mask-pos via liquidate: leave the accrual clock stale so a later interval double-c

## Question
`mask-pos` (mainnet/contracts/market/v0-market-vault.clar:91) maps an asset id to a bit position, offsetting debt bits by DEBT-OFFSET. Can an unprivileged caller of `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382), by choosing `collateral-receiver`, use that to leave the accrual clock stale so a later interval double-counts elapsed time, violating the invariant that the state a safety check approved is the state the money movement executes against and producing direct theft of user funds at rest or in motion?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:91` -> `mask-pos`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `collateral-receiver`
- Exploit idea: `mask-pos` maps an asset id to a bit position, offsetting debt bits by DEBT-OFFSET. Reach it through `liquidate` and leave the accrual clock stale so a later interval double-counts elapsed time.
- Invariant to test: the state a safety check approved is the state the money movement executes against
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate` with `collateral-receiver`, then read `mask-pos` state before and after in the same block and assert the two sides of the invariant are equal.
