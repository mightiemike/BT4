# Q2323: calc-liq-debt-repay via liquidate: strand value on the market contract when a later step of a

## Question
`calc-liq-debt-repay` (mainnet/contracts/market/v0-4-market.clar:723) takes the liquidation factor times the debt with `mul-bps-down`. Can an unprivileged caller of `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382), by choosing `min-collateral-expected`, use that to strand value on the market contract when a later step of a composite call fails, violating the invariant that a failed sub-step aborts the transaction or is explicitly compensated and producing direct theft of user funds at rest or in motion?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:723` -> `calc-liq-debt-repay`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `min-collateral-expected`
- Exploit idea: `calc-liq-debt-repay` takes the liquidation factor times the debt with `mul-bps-down`. Reach it through `liquidate` and strand value on the market contract when a later step of a composite call fails.
- Invariant to test: a failed sub-step aborts the transaction or is explicitly compensated
- Expected Immunefi impact: Critical - direct theft of user funds at rest or in motion
- Fast validation: In `local-testing/tests` on a local fork, drive `liquidate` with `min-collateral-expected`, then read `calc-liq-debt-repay` state before and after in the same block and assert the two sides of the invariant are equal.
